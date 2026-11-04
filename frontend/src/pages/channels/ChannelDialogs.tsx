import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import { errorMessage } from "../../api/errors";
import { invalidate, keys, useDeliveries } from "../../api/queries";
import type { Channel } from "../../api/types";
import { KIND_INFO } from "./kinds";
import type { Kind } from "./kinds";
import { formatDateTime } from "../../lib/format";
import { idempotencyKey } from "../../lib/ids";
import { Button } from "../../ui/Button";
import { RadioCards } from "../../ui/Controls";
import { Dialog } from "../../ui/Dialog";
import { Skeleton } from "../../ui/Feedback";
import { FormError, TextField } from "../../ui/Field";
import { toast } from "../../ui/toast-store";

export function ChannelDialog({
  open,
  onClose,
  channel,
}: {
  open: boolean;
  onClose: () => void;
  channel: Channel | null;
}) {
  const client = useQueryClient();
  const [kind, setKind] = useState<Kind>("slack");
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [key, setKey] = useState(idempotencyKey);

  useEffect(() => {
    if (!open) return;
    setKind((channel?.type as Kind | undefined) ?? "slack");
    setName(channel?.name ?? "");
    setTarget("");
    setSecret("");
    setError(null);
    setKey(idempotencyKey());
  }, [open, channel]);

  const config = (): Record<string, string> => {
    if (kind === "email") return { email: target.trim() };
    if (kind === "webhook")
      return { url: target.trim(), secret: secret || (channel ? "********" : "") };
    if (kind === "ntfy" && secret) return { url: target.trim(), token: secret };
    return { url: target.trim() };
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      if (channel) {
        await apiRequest(`/channels/${channel.id}`, {
          method: "PATCH",
          body: target.trim()
            ? { name: name.trim() || channel.name, config: config() }
            : { name: name.trim() || channel.name },
        });
        toast(target.trim() ? "Новый адрес отправлен на подтверждение" : "Канал сохранён");
      } else {
        const created = await apiRequest<Channel>("/channels", {
          method: "POST",
          idempotencyKey: key,
          body: { type: kind, name: name.trim() || KIND_INFO[kind].label, config: config() },
        });
        if (!created.verified_at)
          await apiRequest(`/channels/${created.id}/verify`, { method: "POST" });
        toast(created.verified_at ? "Канал подключён" : "Канал добавлен. Отправили подтверждение");
      }
      invalidate(client, keys.channels);
      onClose();
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={channel ? `Канал «${channel.name}»` : "Новый канал"}
      description={
        channel
          ? "Пустой адрес оставит текущий. Новый адрес начнёт работать после подтверждения."
          : undefined
      }
      footer={
        <>
          <Button onClick={onClose}>Отмена</Button>
          <Button variant="primary" loading={busy} onClick={() => void save()}>
            {channel ? "Сохранить" : "Подключить"}
          </Button>
        </>
      }
    >
      <form
        className="grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        {!channel ? (
          <RadioCards<Kind>
            label="Тип канала"
            columns="grid-cols-2 sm:grid-cols-3"
            value={kind}
            onChange={setKind}
            options={(Object.keys(KIND_INFO) as Kind[]).map((value) => ({
              value,
              label: KIND_INFO[value].label,
            }))}
          />
        ) : null}
        <TextField
          label="Название"
          placeholder={KIND_INFO[kind].label}
          value={name}
          maxLength={100}
          onChange={(event) => setName(event.target.value)}
        />
        <TextField
          label={kind === "email" ? "Адрес почты" : "Адрес вебхука"}
          type={kind === "email" ? "email" : "url"}
          placeholder={channel ? "оставьте пустым, чтобы не менять" : KIND_INFO[kind].placeholder}
          value={target}
          onChange={(event) => setTarget(event.target.value)}
          hint={KIND_INFO[kind].hint}
        />
        {kind === "webhook" || kind === "ntfy" ? (
          <TextField
            label={kind === "webhook" ? "Секрет подписи" : "Токен доступа"}
            type="password"
            autoComplete="off"
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            hint={
              kind === "webhook"
                ? "16-256 символов. Храните его на стороне приёмника."
                : "Необязательно"
            }
          />
        ) : null}
        <FormError message={error} />
      </form>
    </Dialog>
  );
}

const STATUS_TEXT: Record<string, string> = {
  sent: "доставлено",
  pending: "в очереди",
  failed: "ошибка",
  canceled: "отменено",
};

export function DeliveriesDialog({
  channel,
  onClose,
}: {
  channel: Channel | null;
  onClose: () => void;
}) {
  const deliveries = useDeliveries(channel?.id ?? 0, channel !== null);
  return (
    <Dialog
      open={channel !== null}
      onClose={onClose}
      title={`Доставки: ${channel?.name ?? ""}`}
      size="lg"
    >
      {deliveries.isPending ? (
        <Skeleton className="h-40" />
      ) : deliveries.data?.length ? (
        <div tabIndex={0} role="region" aria-label="Таблица" className="table-scroll">
          <table className="table min-w-[560px]">
            <thead>
              <tr>
                <th scope="col">Создано</th>
                <th scope="col">Событие</th>
                <th scope="col">Статус</th>
                <th scope="col" className="text-right">
                  Попытки
                </th>
                <th scope="col">Ответ</th>
              </tr>
            </thead>
            <tbody>
              {deliveries.data.map((item) => (
                <tr key={item.id}>
                  <td className="whitespace-nowrap">{formatDateTime(item.created_at)}</td>
                  <td>{item.event ?? "н/д"}</td>
                  <td
                    className={
                      item.status === "sent"
                        ? "text-accent"
                        : item.status === "failed"
                          ? "text-down"
                          : "text-inkMuted"
                    }
                  >
                    {STATUS_TEXT[item.status] ?? item.status}
                  </td>
                  <td className="text-right">{item.attempts}</td>
                  <td className="text-inkMuted">{item.response_code ?? item.error ?? "н/д"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-[13px] text-inkMuted">
          Доставок ещё не было. Отправьте тестовое уведомление.
        </p>
      )}
    </Dialog>
  );
}
