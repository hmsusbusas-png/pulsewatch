import type { ChannelCreate } from "../../api/types";

export type Kind = ChannelCreate["type"];

export const KIND_INFO: Record<Kind, { label: string; hint: string; placeholder: string }> = {
  email: {
    label: "Email",
    hint: "Чужой адрес заработает после перехода по ссылке из письма",
    placeholder: "oncall@example.com",
  },
  slack: {
    label: "Slack",
    hint: "Incoming Webhook из настроек приложения Slack",
    placeholder: "https://hooks.slack.com/services/…",
  },
  discord: {
    label: "Discord",
    hint: "Интеграции канала → Вебхуки → Копировать URL",
    placeholder: "https://discord.com/api/webhooks/…",
  },
  teams: {
    label: "Microsoft Teams",
    hint: "Workflows → «Post to a channel when a webhook request is received»",
    placeholder: "https://prod-00.westeurope.logic.azure.com/…",
  },
  ntfy: {
    label: "ntfy",
    hint: "Адрес темы; токен нужен для защищённой темы",
    placeholder: "https://ntfy.sh/my-alerts",
  },
  webhook: {
    label: "Webhook",
    hint: "POST JSON с подписью X-Pulsewatch-Signature: sha256=HMAC(секрет, тело)",
    placeholder: "https://ops.example.com/hooks/pulsewatch",
  },
};
