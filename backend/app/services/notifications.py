import asyncio
import hashlib
import hmac
import json
import logging
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crypto import decrypt_config, decrypt_text, encrypt_text
from app.errors import ApiError
from app.models import (
    MailJob,
    NotificationChannel,
    NotificationDelivery,
    NotificationTemplate,
    Plan,
    Site,
    SiteChannel,
    StatusPage,
    StatusPageSite,
    StatusSubscriber,
    Subscription,
    User,
)
from app.security import link_claims, signed_link
from app.services.channel_formats import WEBHOOK_TYPES, build_request
from app.services.probe import RedirectRejected, safe_request
from app.services.templates import render_template

logger = logging.getLogger("pulsewatch.mail")


def unsubscribe_token(channel_id: int) -> str:
    return signed_link("unsubscribe", {"channel": channel_id}, 3600)


def token_channel(token: str) -> int | None:
    claims = link_claims(token, "unsubscribe")
    return int(str(claims["channel"])) if claims else None


def frontend_link(path: str, token: str) -> str:
    return f"{settings.frontend_base_url.rstrip('/')}{path}?token={token}"


def _smtp(address: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        raise ConnectionError("SMTP не настроен")
    if not settings.smtp_starttls:
        raise ConnectionError("SMTP требует TLS")
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = address
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def send_email(address: str, subject: str, body: str) -> None:
    if not settings.smtp_host and settings.app_env == "dev":
        logger.info(
            "dev_mail",
            extra={"dev_mail": {"to": address, "subject": subject, "body": body}},
        )
        return
    await asyncio.to_thread(_smtp, address, subject, body)


async def _templates(session: AsyncSession, user_id: int) -> dict[str, str]:
    features = await session.scalar(
        select(Plan.features)
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(Subscription.user_id == user_id)
    )
    if not features or not features.get("templates"):
        return {}
    rows = await session.execute(
        select(NotificationTemplate.event, NotificationTemplate.body).where(
            NotificationTemplate.user_id == user_id
        )
    )
    return dict(rows.tuples().all())


async def enqueue(
    session: AsyncSession,
    site: Site,
    text: str,
    event: str | None = None,
    context: dict[str, str] | None = None,
) -> None:
    if event and context:
        custom = (await _templates(session, site.user_id)).get(event)
        if custom:
            text = render_template(custom, context)
    subscribers = await session.execute(
        select(StatusSubscriber, StatusPage)
        .join(StatusPage, StatusPage.id == StatusSubscriber.page_id)
        .join(StatusPageSite, StatusPageSite.page_id == StatusPage.id)
        .where(StatusPageSite.site_id == site.id, StatusSubscriber.confirmed.is_(True))
    )
    for subscriber, page in subscribers:
        token = signed_link("status_unsubscribe", {"subscriber": subscriber.id}, 86400 * 30)
        unsubscribe_url = frontend_link("/public/subscribers/unsubscribe", token)
        session.add(
            MailJob(
                user_id=page.user_id,
                recipient=subscriber.email,
                subject=f"Статус: {page.title}",
                body=encrypt_text(f"{text}\n\nОтписаться: {unsubscribe_url}"),
            )
        )
    channels = await session.scalars(
        select(NotificationChannel)
        .join(SiteChannel, SiteChannel.channel_id == NotificationChannel.id)
        .where(
            SiteChannel.site_id == site.id,
            NotificationChannel.user_id == site.user_id,
            NotificationChannel.enabled.is_(True),
            NotificationChannel.verified_at.is_not(None),
        )
    )
    user = await session.get(User, site.user_id)
    for channel in channels:
        if (
            channel.type == "telegram"
            and user
            and user.telegram_mute_until
            and user.telegram_mute_until > datetime.now(UTC)
        ):
            continue
        session.add(
            NotificationDelivery(
                channel_id=channel.id,
                payload={
                    "site_id": site.id,
                    "name": site.name,
                    "text": text,
                    "event": event,
                    "context": context or {},
                },
            )
        )


def direct_delivery(channel_id: int, site: Site, text: str, event: str) -> NotificationDelivery:
    return NotificationDelivery(
        channel_id=channel_id,
        payload={"site_id": site.id, "name": site.name, "text": text, "event": event},
    )


async def _deliver_email(
    channel: NotificationChannel, config: dict[str, str], delivery: NotificationDelivery
) -> int:
    text = str(delivery.payload.get("text", ""))
    if delivery.payload.get("event") == "verification":
        token = signed_link("channel_verify", {"channel": channel.id}, 86400)
        await send_email(
            config["email"],
            "Pulsewatch: подтвердите адрес для уведомлений",
            f"Подтвердите получение уведомлений: {frontend_link('/channels/verify', token)}\n"
            "Ссылка действует 24 часа.",
        )
        return 250
    url = frontend_link("/channels/unsubscribe", unsubscribe_token(channel.id))
    await send_email(config["email"], "Pulsewatch: уведомление", f"{text}\n\nОтписаться: {url}")
    return 250


async def _deliver_webhook(
    channel: NotificationChannel, config: dict[str, str], delivery: NotificationDelivery
) -> int:
    if channel.type == "webhook":
        body = json.dumps(delivery.payload, ensure_ascii=False, separators=(",", ":")).encode()
        signature = hmac.new(config["secret"].encode(), body, hashlib.sha256).hexdigest()
        response = await safe_request(
            config["url"],
            allow_private=settings.allow_private_targets,
            method="POST",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-Pulsewatch-Signature": f"sha256={signature}",
            },
            follow=False,
        )
        return response.status_code
    outbound = build_request(channel.type, config, delivery.payload)
    response = await safe_request(
        outbound.url,
        allow_private=settings.allow_private_targets,
        method="POST",
        body=outbound.body,
        headers=outbound.headers,
        follow=False,
    )
    return response.status_code


async def deliver_pending(session: AsyncSession) -> None:
    rows = (
        await session.scalars(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.status == "pending",
                NotificationDelivery.next_attempt_at <= datetime.now(UTC),
            )
            .order_by(NotificationDelivery.id)
            .limit(20)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for delivery in rows:
        channel = await session.get(NotificationChannel, delivery.channel_id)
        if channel is None or not channel.enabled:
            delivery.status = "canceled"
            continue
        delivery.attempts += 1
        retry_after = 0
        verifying = delivery.payload.get("event") == "verification"
        try:
            source = channel.pending_config if verifying and channel.pending_config else None
            config = decrypt_config(source or channel.config)
            text = str(delivery.payload.get("text", ""))
            if channel.type == "telegram":
                from app.services.telegram import telegram_request

                response = await telegram_request(
                    "sendMessage", {"chat_id": int(config["chat_id"]), "text": text}
                )
                code = response.status_code
                if code == 403:
                    channel.enabled = False
                if code == 429:
                    retry_after = int(response.json().get("parameters", {}).get("retry_after", 1))
            elif channel.type in WEBHOOK_TYPES:
                code = await _deliver_webhook(channel, config, delivery)
            else:
                code = await _deliver_email(channel, config, delivery)
            delivery.response_code = code
            if not 200 <= code < 300 and code != 250:
                raise ConnectionError(f"Канал вернул {code}")
            delivery.status = "sent"
            delivery.delivered_at = datetime.now(UTC)
            delivery.error = None
            if channel.type != "email":
                if verifying and channel.pending_config:
                    channel.config, channel.pending_config = channel.pending_config, None
                    channel.verified_at = datetime.now(UTC)
                channel.verified_at = channel.verified_at or datetime.now(UTC)
        except (
            ApiError,
            RedirectRejected,
            httpx.HTTPError,
            OSError,
            ValueError,
            KeyError,
            TimeoutError,
            smtplib.SMTPException,
        ) as exc:
            delivery.error = type(exc).__name__
            delivery.status = "failed" if delivery.attempts >= 5 else "pending"
            delivery.next_attempt_at = datetime.now(UTC) + timedelta(
                seconds=max(retry_after, 2**delivery.attempts * 10)
            )
    await session.commit()


async def deliver_mail(session: AsyncSession) -> None:
    rows = await session.scalars(
        select(MailJob)
        .where(MailJob.status == "pending", MailJob.next_attempt_at <= datetime.now(UTC))
        .order_by(MailJob.id)
        .limit(20)
        .with_for_update(skip_locked=True)
    )
    for job in rows:
        job.attempts += 1
        try:
            await send_email(job.recipient, job.subject, decrypt_text(job.body))
            job.status = "sent"
        except (OSError, smtplib.SMTPException):
            job.status = "failed" if job.attempts >= 5 else "pending"
            job.next_attempt_at = datetime.now(UTC) + timedelta(seconds=2**job.attempts * 30)
    await session.commit()
