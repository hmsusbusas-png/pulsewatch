import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { apiRequest, expireSession } from "../../api/client";
import { ApiError, errorMessage } from "../../api/errors";
import { useEnvironment } from "../../api/queries";
import { useAuth } from "../../auth/auth-context";
import { Logo } from "../../app/Logo";
import { Button, ButtonLink } from "../../ui/Button";
import { FormError, TextField } from "../../ui/Field";

export type AuthMode =
  | "login"
  | "register"
  | "forgot"
  | "signup"
  | "reset"
  | "email"
  | "subscriber-confirm"
  | "subscriber-unsubscribe"
  | "channel-unsubscribe"
  | "channel-verify";

const TITLES: Record<AuthMode, string> = {
  login: "Вход",
  register: "Новый аккаунт",
  forgot: "Восстановление пароля",
  signup: "Подтверждение регистрации",
  reset: "Новый пароль",
  email: "Смена почты",
  "subscriber-confirm": "Подтверждение подписки",
  "subscriber-unsubscribe": "Отписка от обновлений",
  "channel-unsubscribe": "Отписка от уведомлений",
  "channel-verify": "Подтверждение адреса",
};

const TOKEN_ENDPOINTS: Partial<Record<AuthMode, string>> = {
  email: "/account/email/confirm",
  "subscriber-confirm": "/public/subscribers/confirm",
  "subscriber-unsubscribe": "/public/subscribers/unsubscribe",
  "channel-unsubscribe": "/channels/unsubscribe",
  "channel-verify": "/channels/verify",
};

const DONE: Partial<Record<AuthMode, string>> = {
  register: "Если адрес свободен, на него придёт письмо со ссылкой. По ней вы зададите пароль.",
  forgot:
    "Если аккаунт существует, на почту придёт ссылка для смены пароля. Она действует 30 минут.",
  email: "Почта изменена. Войдите снова с новым адресом.",
  "subscriber-confirm": "Подписка подтверждена. Сообщим об инцидентах на эту почту.",
  "subscriber-unsubscribe": "Вы отписались от обновлений статус-страницы.",
  "channel-unsubscribe": "Уведомления на этот адрес отключены.",
  "channel-verify": "Адрес подтверждён. Уведомления о сбоях будут приходить на него.",
};

export function AuthPage({ mode }: { mode: AuthMode }) {
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const environment = useEnvironment();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [now, setNow] = useState(Date.now());
  const token = params.get("token") ?? "";
  const needsToken = !["login", "register", "forgot"].includes(mode);
  const badToken = needsToken && (token.length < 20 || token.length > 2048);
  const withEmail = ["login", "register", "forgot"].includes(mode);
  const withPassword = ["login", "register", "signup", "reset"].includes(mode);
  const confirmed = (location.state as { confirmed?: boolean } | null)?.confirmed;
  const wait =
    error instanceof ApiError && error.retryAt
      ? Math.max(0, Math.ceil((error.retryAt - now) / 1000))
      : 0;

  useEffect(() => {
    document.title = `${TITLES[mode]}. pulsewatch`;
    if (!wait) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [mode, wait]);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") {
        const result = await apiRequest<{ access_token: string }>("/auth/login", {
          method: "POST",
          body: { email: email.trim(), password },
          authenticated: false,
        });
        signIn(result.access_token);
        const from = (location.state as { from?: unknown } | null)?.from;
        navigate(
          typeof from === "string" && /^\/(?![\\/])/.test(from) && !from.includes("\\")
            ? from
            : "/",
          { replace: true },
        );
        return;
      }
      if (mode === "register" || mode === "forgot") {
        await apiRequest(mode === "register" ? "/auth/register" : "/auth/password/reset", {
          method: "POST",
          body: mode === "register" ? { email: email.trim(), password } : { email: email.trim() },
          authenticated: false,
        });
      } else if (mode === "signup" || mode === "reset") {
        await apiRequest(mode === "signup" ? "/auth/email/confirm" : "/auth/password/confirm", {
          method: "POST",
          body: { token, password },
          authenticated: false,
        });
        expireSession();
        navigate("/login", { replace: true, state: { confirmed: true } });
        return;
      } else {
        await apiRequest(TOKEN_ENDPOINTS[mode] ?? "", {
          method: "POST",
          body: { token },
          authenticated: false,
        });
        if (mode === "email") expireSession();
      }
      setDone(true);
    } catch (problem) {
      setError(
        mode === "login" &&
          problem instanceof ApiError &&
          [400, 401, 403, 404].includes(problem.status)
          ? new ApiError(
              problem.status,
              "invalid_credentials",
              "Неверный email или пароль",
              problem.requestId,
            )
          : problem,
      );
    } finally {
      setBusy(false);
    }
  };

  const action =
    mode === "login"
      ? "Войти"
      : mode === "register"
        ? "Создать аккаунт"
        : mode === "forgot"
          ? "Прислать ссылку"
          : mode.includes("unsubscribe")
            ? "Отписаться"
            : withPassword
              ? "Сохранить пароль"
              : "Подтвердить";

  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <main className="w-full max-w-[420px]">
        <Link to="/" aria-label="pulsewatch">
          <Logo />
        </Link>
        <div className="panel mt-6 sm:p-7">
          <h1 className="text-xl font-semibold tracking-[-0.02em]">
            {done && withEmail ? "Проверьте почту" : TITLES[mode]}
          </h1>
          {confirmed && mode === "login" ? (
            <p className="mt-2 rounded border border-accent/30 bg-accent/10 px-3 py-2 text-[13px]">
              Готово. Войдите с новым паролем.
            </p>
          ) : null}
          {done ? (
            <div className="mt-4 grid gap-4">
              <p className="text-[13px] leading-relaxed text-inkMuted">{DONE[mode]}</p>
              {withEmail ? (
                <Button loading={busy} disabled={wait > 0} onClick={() => void submit()}>
                  {wait ? `Повторить через ${wait} с` : "Отправить ещё раз"}
                </Button>
              ) : null}
              <ButtonLink to="/login" variant="primary">
                Ко входу
              </ButtonLink>
            </div>
          ) : (
            <form className="mt-5 grid gap-4" onSubmit={(event) => void submit(event)} noValidate>
              {withEmail ? (
                <TextField
                  label="Электронная почта"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  hint={
                    mode === "login" && environment.data === "dev"
                      ? "Демо-аккаунт: demo@example.com, пароль печатает python -m app.seed --demo"
                      : undefined
                  }
                />
              ) : null}
              {withPassword ? (
                <TextField
                  label={mode === "login" ? "Пароль" : "Новый пароль"}
                  type="password"
                  required
                  minLength={mode === "login" ? 1 : 8}
                  maxLength={72}
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  hint={mode === "login" ? undefined : "От 8 до 72 символов"}
                />
              ) : null}
              {badToken ? (
                <FormError message="Ссылка повреждена или устарела. Запросите новое письмо." />
              ) : null}
              {error ? <FormError message={errorMessage(error)} /> : null}
              <Button
                type="submit"
                variant="primary"
                loading={busy}
                disabled={wait > 0 || badToken}
                className="w-full"
              >
                {wait ? `Повторите через ${wait} с` : action}
              </Button>
              {mode === "login" ? (
                <Link
                  to="/forgot-password"
                  className="text-center text-[13px] text-inkMuted hover:text-ink"
                >
                  Забыли пароль?
                </Link>
              ) : null}
            </form>
          )}
        </div>
        <p className="mt-5 text-center text-[13px] text-inkMuted">
          {mode === "login" ? (
            <>
              Нет аккаунта?{" "}
              <Link to="/register" className="text-ink underline">
                Регистрация
              </Link>
            </>
          ) : (
            <>
              Уже есть аккаунт?{" "}
              <Link to="/login" className="text-ink underline">
                Войти
              </Link>
            </>
          )}
          <span className="mx-2">·</span>
          <Link to="/pricing" className="hover:text-ink">
            Тарифы
          </Link>
        </p>
      </main>
    </div>
  );
}
