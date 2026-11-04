import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Database, Radar } from "lucide-react";
import { apiRequest } from "../../api/client";
import { invalidate, keys, useEnvironment, useSubscription } from "../../api/queries";
import type { Site } from "../../api/types";
import { errorMessage } from "../../api/errors";
import { Button } from "../../ui/Button";
import { FormError } from "../../ui/Field";
import { notifyError, toast } from "../../ui/toast-store";
import { idempotencyKey } from "../../lib/ids";
import { suggestName } from "./actions";

export function QuickStart() {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const client = useQueryClient();
  const environment = useEnvironment();
  const subscription = useSubscription();

  const start = async () => {
    const value = url.trim();
    if (!value) {
      setError("Укажите адрес сайта или API, например https://example.com");
      return;
    }
    const target = value.includes("://") ? value : `https://${value}`;
    setBusy(true);
    setError(null);
    try {
      const site = await apiRequest<Site>("/sites", {
        method: "POST",
        idempotencyKey: idempotencyKey(),
        body: {
          name: suggestName(target),
          url: target,
          interval_seconds: Math.max(60, subscription.data?.plan.min_interval_sec ?? 300),
        },
      });
      invalidate(client, keys.sites, keys.dashboard, keys.subscription);
      navigate(`/sites/${site.id}?new=1`);
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };

  const seed = async () => {
    setSeeding(true);
    try {
      await apiRequest("/dev/demo-data", { method: "POST" });
      toast("Добавлены демо-данные за 90 дней");
      await client.invalidateQueries();
    } catch (problem) {
      notifyError(problem);
    } finally {
      setSeeding(false);
    }
  };

  return (
    <section className="mx-auto max-w-2xl py-10 text-center sm:py-16">
      <span className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-full border border-accent/30 bg-accent/10 text-accent">
        <Radar size={22} aria-hidden="true" />
      </span>
      <h1 className="page-title">Что будем отслеживать?</h1>
      <p className="mx-auto mt-2 max-w-lg text-inkMuted">
        Вставьте адрес, и первая проверка начнётся сразу. Уведомления придут в выбранные каналы,
        увидите историю отклика и сможете показать статус клиентам.
      </p>
      <form
        className="mx-auto mt-7 flex max-w-xl flex-col gap-2 sm:flex-row"
        onSubmit={(event) => {
          event.preventDefault();
          void start();
        }}
      >
        <label htmlFor="quick-url" className="sr-only">
          Адрес сайта или API
        </label>
        <input
          id="quick-url"
          className="field h-11 flex-1 text-[15px]"
          placeholder="https://shop.example.com"
          inputMode="url"
          autoComplete="url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          autoFocus
        />
        <Button type="submit" variant="primary" className="h-11 px-5" loading={busy}>
          Начать следить
        </Button>
      </form>
      <div className="mx-auto mt-3 max-w-xl text-left">
        <FormError message={error} />
      </div>
      <p className="mt-4 text-xs text-inkMuted">
        HTTP с проверкой кода 2xx-3xx, подтверждение сбоя двумя проверками. Всё можно изменить
        позже.
      </p>
      {environment.data === "dev" ? (
        <div className="mx-auto mt-10 max-w-md rounded-lg border border-dashed border-lineStrong p-4">
          <p className="text-[13px] text-inkMuted">
            Режим разработки: можно заполнить аккаунт демонстрационной историей за 90 дней: шесть
            мониторов, инциденты, каналы и статус-страница.
          </p>
          <Button
            className="mt-3"
            loading={seeding}
            onClick={() => void seed()}
            icon={<Database size={15} aria-hidden="true" />}
          >
            Показать демо-данные
          </Button>
        </div>
      ) : null}
    </section>
  );
}
