import { ButtonLink } from "../ui/Button";

export function NotFound() {
  return (
    <main className="flex min-h-[60vh] flex-col items-center justify-center px-4 text-center">
      <p className="mono text-xs text-inkMuted">404</p>
      <h1 className="page-title mt-2">Страница не найдена</h1>
      <p className="mt-2 max-w-md text-[13px] text-inkMuted">
        Ссылка устарела или адрес набран с ошибкой.
      </p>
      <ButtonLink to="/" variant="primary" className="mt-5">
        К журналу
      </ButtonLink>
    </main>
  );
}
