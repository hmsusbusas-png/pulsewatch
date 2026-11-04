import { Dialog } from "../ui/Dialog";
import { Kbd } from "../ui/Feedback";

const GROUPS = [
  {
    title: "Переходы",
    items: [
      [["G", "J"], "Журнал мониторов"],
      [["G", "I"], "Инциденты"],
      [["G", "S"], "Статус-страницы"],
      [["G", "C"], "Каналы"],
      [["G", "T"], "Тарифы"],
    ],
  },
  {
    title: "Действия",
    items: [
      [["Ctrl", "K"], "Командная палитра"],
      [["N"], "Новый монитор"],
      [["/"], "Поиск по мониторам"],
      [["?"], "Эта справка"],
      [["Esc"], "Закрыть окно"],
    ],
  },
] as const;

export function ShortcutsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onClose={onClose} title="Горячие клавиши" size="sm">
      <div className="grid gap-5">
        {GROUPS.map((group) => (
          <section key={group.title}>
            <h3 className="eyebrow mb-2">{group.title}</h3>
            <dl className="grid gap-1.5">
              {group.items.map(([combo, label]) => (
                <div key={label} className="flex items-center justify-between gap-3 text-[13px]">
                  <dt>{label}</dt>
                  <dd className="flex items-center gap-1">
                    {combo.map((key, index) => (
                      <span key={key} className="flex items-center gap-1">
                        {index ? <span className="text-2xs text-inkMuted">затем</span> : null}
                        <Kbd>{key}</Kbd>
                      </span>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
        <p className="text-xs text-inkMuted">На macOS вместо Ctrl используйте ⌘.</p>
      </div>
    </Dialog>
  );
}
