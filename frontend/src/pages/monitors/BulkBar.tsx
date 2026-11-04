import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FolderInput, Pause, Play, Tags, Trash2, X } from "lucide-react";
import { apiRequest } from "../../api/client";
import { invalidate, keys, useChannels } from "../../api/queries";
import type { BulkAction, BulkResult, Site } from "../../api/types";
import { countLabel } from "../../lib/format";
import { Button } from "../../ui/Button";
import { Checkbox } from "../../ui/Controls";
import { Dialog } from "../../ui/Dialog";
import { TextField } from "../../ui/Field";
import { deferRemoval, notifyError, toast } from "../../ui/toast-store";

type Editing = "tags" | "group" | "channels" | null;

export function BulkBar({ selected, onClear }: { selected: Site[]; onClear: () => void }) {
  const client = useQueryClient();
  const channels = useChannels();
  const [busy, setBusy] = useState<BulkAction | null>(null);
  const [editing, setEditing] = useState<Editing>(null);
  const [text, setText] = useState("");
  const [chosen, setChosen] = useState<number[]>([]);
  const ids = selected.map((site) => site.id);

  const run = async (action: BulkAction, extra: Record<string, unknown> = {}) => {
    setBusy(action);
    try {
      const result = await apiRequest<BulkResult>("/sites/bulk", {
        method: "POST",
        body: { ids, action, ...extra },
      });
      const failed = result.results.filter((item) => !item.ok);
      toast(
        failed.length
          ? `Готово: ${result.updated}, с ошибкой: ${failed.length} (${failed[0].error})`
          : `Изменено: ${countLabel(result.updated, "монитор", "монитора", "мониторов")}`,
        { tone: failed.length ? "error" : "success" },
      );
      invalidate(client, keys.sites, keys.dashboard);
      setEditing(null);
      onClear();
    } catch (error) {
      notifyError(error);
    } finally {
      setBusy(null);
    }
  };

  const remove = () => {
    const snapshot = client.getQueryData<Site[]>(keys.sites);
    const doomed = new Set(ids);
    deferRemoval({
      message: `Удалено: ${countLabel(ids.length, "монитор", "монитора", "мониторов")}`,
      hide: () => {
        client.setQueryData<Site[]>(keys.sites, (sites) =>
          sites?.filter((site) => !doomed.has(site.id)),
        );
        onClear();
      },
      restore: () => client.setQueryData(keys.sites, snapshot),
      commit: async () => {
        await apiRequest("/sites/bulk", {
          method: "POST",
          body: { ids: [...doomed], action: "delete" },
          keepalive: true,
        });
        invalidate(client, keys.dashboard, keys.subscription, keys.daily);
      },
    });
  };

  return (
    <>
      <div
        role="toolbar"
        aria-label="Действия с выбранными мониторами"
        className="fixed inset-x-3 bottom-[calc(env(safe-area-inset-bottom)+68px)] z-40 mx-auto flex max-w-3xl flex-wrap items-center gap-1.5 rounded-lg border border-lineStrong bg-raised px-3 py-2 shadow-pop lg:bottom-5 lg:left-[232px]"
        style={{ animation: "rise 160ms ease-out" }}
      >
        <span className="mr-auto px-1 text-[13px] font-medium">Выбрано: {selected.length}</span>
        <Button
          size="sm"
          variant="ghost"
          loading={busy === "pause"}
          onClick={() => void run("pause")}
          icon={<Pause size={14} aria-hidden="true" />}
        >
          Пауза
        </Button>
        <Button
          size="sm"
          variant="ghost"
          loading={busy === "resume"}
          onClick={() => void run("resume")}
          icon={<Play size={14} aria-hidden="true" />}
        >
          Возобновить
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setText("");
            setEditing("tags");
          }}
          icon={<Tags size={14} aria-hidden="true" />}
        >
          Теги
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setText("");
            setEditing("group");
          }}
          icon={<FolderInput size={14} aria-hidden="true" />}
        >
          Группа
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setChosen([]);
            setEditing("channels");
          }}
        >
          Каналы
        </Button>
        <Button
          size="sm"
          variant="danger"
          onClick={remove}
          icon={<Trash2 size={14} aria-hidden="true" />}
        >
          Удалить
        </Button>
        <Button size="icon-sm" variant="ghost" onClick={onClear} aria-label="Снять выделение">
          <X size={15} aria-hidden="true" />
        </Button>
      </div>
      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        size="sm"
        title={
          editing === "tags"
            ? "Заменить теги"
            : editing === "group"
              ? "Перенести в группу"
              : "Назначить каналы"
        }
        description={`Для ${countLabel(ids.length, "монитора", "мониторов", "мониторов")}`}
        footer={
          <>
            <Button onClick={() => setEditing(null)}>Отмена</Button>
            <Button
              variant="primary"
              loading={busy !== null}
              onClick={() => {
                if (editing === "tags")
                  void run("set_tags", {
                    tags: text
                      .split(",")
                      .map((tag) => tag.trim())
                      .filter(Boolean),
                  });
                else if (editing === "group") void run("set_group", { group: text.trim() || null });
                else void run("set_channels", { channel_ids: chosen });
              }}
            >
              Применить
            </Button>
          </>
        }
      >
        {editing === "channels" ? (
          <div className="grid gap-0.5">
            {(channels.data ?? []).map((channel) => (
              <Checkbox
                key={channel.id}
                checked={chosen.includes(channel.id)}
                onChange={(next) =>
                  setChosen((value) =>
                    next ? [...value, channel.id] : value.filter((id) => id !== channel.id),
                  )
                }
                label={channel.name}
                description={channel.target ?? channel.type}
              />
            ))}
            {channels.data?.length === 0 ? (
              <p className="text-[13px] text-inkMuted">Каналов пока нет.</p>
            ) : null}
          </div>
        ) : (
          <TextField
            label={editing === "tags" ? "Теги через запятую" : "Название группы"}
            hint={
              editing === "group"
                ? "Пустое поле уберёт мониторы из групп"
                : "Текущие теги будут заменены"
            }
            value={text}
            maxLength={editing === "tags" ? 500 : 100}
            onChange={(event) => setText(event.target.value)}
          />
        )}
      </Dialog>
    </>
  );
}
