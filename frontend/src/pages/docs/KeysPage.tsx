import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus } from "lucide-react";
import { apiRequest } from "../../api/client";
import { errorMessage } from "../../api/errors";
import { invalidate, keys, useApiKeys, useSubscription } from "../../api/queries";
import type { ApiKey, ApiKeyCreated } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import { idempotencyKey } from "../../lib/ids";
import { Button, ButtonLink } from "../../ui/Button";
import { Segmented } from "../../ui/Controls";
import { Dialog } from "../../ui/Dialog";
import { CopyField, EmptyState, PlanLock, Skeleton, Time } from "../../ui/Feedback";
import { FormError, TextField } from "../../ui/Field";
import { deferRemoval } from "../../ui/toast-store";

export function KeysPage() {
  const subscription = useSubscription();
  const allowed = Boolean(subscription.data?.plan.features.api_keys);
  const list = useApiKeys(allowed);
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<"read" | "write">("read");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    document.title = "API-ключи. pulsewatch";
  }, []);
  const revoke = (key: ApiKey) => {
    const before = client.getQueryData<ApiKey[]>(keys.apiKeys);
    deferRemoval({
      message: `Ключ «${key.name}» отозван`,
      hide: () =>
        client.setQueryData<ApiKey[]>(keys.apiKeys, (rows) =>
          rows?.map((row) => (row.id === key.id ? { ...row, revoked: true } : row)),
        ),
      restore: () => client.setQueryData(keys.apiKeys, before),
      commit: async () => {
        await apiRequest(`/keys/${key.id}`, { method: "DELETE", keepalive: true });
        invalidate(client, keys.apiKeys);
      },
    });
  };
  return (
    <>
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">API-ключи</h1>
          <p className="mt-1 text-[13px] text-inkMuted">
            Доступ к мониторам, инцидентам, отчётам и выгрузкам из CI и скриптов.
          </p>
        </div>
        <div className="flex gap-2">
          <ButtonLink to="/api-docs" size="sm">
            Документация
          </ButtonLink>
          {allowed ? (
            <Button
              size="sm"
              variant="primary"
              onClick={() => {
                setCreated(null);
                setName("");
                setError(null);
                setOpen(true);
              }}
              icon={<Plus size={14} aria-hidden="true" />}
            >
              Новый ключ
            </Button>
          ) : null}
        </div>
      </header>
      {subscription.isPending ? (
        <Skeleton className="h-32 rounded-lg" />
      ) : !allowed ? (
        <PlanLock feature="API-ключи" />
      ) : (
        <section className="surface overflow-hidden" aria-label="Ключи">
          {list.isPending ? (
            <div className="p-4">
              <Skeleton className="h-20" />
            </div>
          ) : list.data?.length ? (
            <div tabIndex={0} role="region" aria-label="Таблица" className="table-scroll">
              <table className="table min-w-[620px]">
                <thead>
                  <tr>
                    <th scope="col">Название</th>
                    <th scope="col">Префикс</th>
                    <th scope="col">Права</th>
                    <th scope="col">Создан</th>
                    <th scope="col">Использован</th>
                    <th scope="col">
                      <span className="sr-only">Действия</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.map((key) => (
                    <tr key={key.id} className={key.revoked ? "opacity-50" : ""}>
                      <td className="font-medium">{key.name}</td>
                      <td className="mono">{key.prefix}…</td>
                      <td>{key.scope === "write" ? "чтение и запись" : "только чтение"}</td>
                      <td>{formatDateTime(key.created_at ?? null)}</td>
                      <td>
                        <Time value={key.last_used_at} />
                      </td>
                      <td className="text-right">
                        {key.revoked ? (
                          <span className="text-xs text-inkMuted">отозван</span>
                        ) : (
                          <Button size="xs" variant="ghost" onClick={() => revoke(key)}>
                            Отозвать
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={<KeyRound size={18} aria-hidden="true" />}
              title="Ключей нет"
              action={
                <Button variant="primary" onClick={() => setOpen(true)}>
                  Выпустить ключ
                </Button>
              }
            >
              Ключ с правами только на чтение подойдёт для дашбордов, с правами на запись для
              создания мониторов из CI при деплое.
            </EmptyState>
          )}
        </section>
      )}
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        size="sm"
        title={created ? "Ключ выпущен" : "Новый ключ"}
        footer={
          created ? (
            <Button variant="primary" onClick={() => setOpen(false)}>
              Готово
            </Button>
          ) : (
            <>
              <Button onClick={() => setOpen(false)}>Отмена</Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={!name.trim()}
                onClick={async () => {
                  setBusy(true);
                  setError(null);
                  try {
                    setCreated(
                      await apiRequest<ApiKeyCreated>("/keys", {
                        method: "POST",
                        body: { name: name.trim(), scope },
                        idempotencyKey: idempotencyKey(),
                      }),
                    );
                    invalidate(client, keys.apiKeys);
                  } catch (problem) {
                    setError(errorMessage(problem));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Выпустить
              </Button>
            </>
          )
        }
      >
        {created ? (
          <div className="grid gap-3">
            <CopyField label="Скопируйте ключ. Он показывается один раз" value={created.token} />
            <pre className="mono overflow-x-auto rounded border border-line bg-sidebar p-3">{`curl -H "Authorization: Bearer ${created.token}" ${window.location.origin}/api/sites`}</pre>
          </div>
        ) : (
          <div className="grid gap-4">
            <TextField
              label="Название"
              placeholder="CI: деплой"
              value={name}
              maxLength={100}
              onChange={(event) => setName(event.target.value)}
            />
            <div>
              <p className="label">Права</p>
              <Segmented<"read" | "write">
                label="Права ключа"
                value={scope}
                onChange={setScope}
                options={[
                  { value: "read", label: "Только чтение" },
                  { value: "write", label: "Чтение и запись" },
                ]}
              />
            </div>
            <FormError message={error} />
          </div>
        )}
      </Dialog>
    </>
  );
}
