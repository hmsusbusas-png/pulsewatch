import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Check, Minus } from "lucide-react";
import { apiRequest } from "../../api/client";
import { errorMessage } from "../../api/errors";
import { usePlans, useSubscription } from "../../api/queries";
import type { Plan } from "../../api/types";
import { Logo } from "../../app/Logo";
import { useAuth } from "../../auth/auth-context";
import { formatDate, formatDuration, formatMoney } from "../../lib/format";
import { Button, ButtonLink } from "../../ui/Button";
import { ErrorPanel, Skeleton } from "../../ui/Feedback";
import { FormError } from "../../ui/Field";

const FEATURES: [string, string][] = [
  ["heartbeat", "Heartbeat для cron-задач"],
  ["keyword", "Проверка ключевого слова"],
  ["headers", "Свои заголовки и POST"],
  ["assertions", "Условия по JSON и заголовкам"],
  ["dns", "DNS-мониторы"],
  ["domain_expiry", "Срок домена через RDAP"],
  ["slo", "SLO, бюджет ошибок, SLA-отчёты"],
  ["escalations", "Эскалации затянувшихся сбоев"],
  ["custom_slug", "Свой адрес и домен статус-страницы"],
  ["maintenance", "Окна обслуживания"],
  ["regions", "Подтверждение из нескольких регионов"],
  ["templates", "Шаблоны уведомлений"],
  ["api_keys", "API-ключи"],
  ["csv", "CSV-выгрузки истории"],
];

function limits(plan: Plan): string[] {
  return [
    `${plan.max_sites} мониторов`,
    `проверка от ${formatDuration(plan.min_interval_sec)}`,
    `история ${plan.retention_days} дней`,
    plan.max_channels === null
      ? "каналы без ограничений"
      : `${plan.max_channels} ${plan.max_channels === 1 ? "канал" : "каналов"} уведомлений`,
    `${plan.max_regions} ${plan.max_regions === 1 ? "регион" : "региона"} проверки`,
  ];
}

export function PricingPage() {
  const plans = usePlans();
  const auth = useAuth();
  const subscription = useSubscription(auth.isAuthenticated);
  const navigate = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    document.title = "Тарифы. pulsewatch";
  }, []);
  const current = subscription.data?.plan.code;
  const choose = async (code: string) => {
    if (!auth.isAuthenticated) {
      navigate("/register");
      return;
    }
    if (
      code === "free" ||
      (current &&
        code !== current &&
        plans.data &&
        (plans.data.find((plan) => plan.code === code)?.price_kop ?? 0) <
          (plans.data.find((plan) => plan.code === current)?.price_kop ?? 0))
    ) {
      navigate("/settings?tab=billing");
      return;
    }
    setBusy(code);
    setError(null);
    try {
      const checkout = await apiRequest<{ payment_id: string; url: string }>("/billing/checkout", {
        method: "POST",
        body: { plan_code: code },
      });
      const url = new URL(checkout.url, window.location.origin);
      if (url.pathname.startsWith("/api/billing/dev/")) {
        sessionStorage.setItem("pw.checkout", `${url.pathname.slice(4)}${url.search}`);
        navigate("/billing/dev");
        return;
      }
      if (url.protocol !== "https:")
        throw new Error("Платёжный провайдер вернул небезопасную ссылку");
      window.location.assign(url.href);
    } catch (problem) {
      setError(errorMessage(problem));
      setBusy(null);
    }
  };
  const ordered = [...(plans.data ?? [])].sort((a, b) => a.price_kop - b.price_kop);
  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-8">
      <div className="mb-10 flex flex-wrap items-center justify-between gap-4">
        <Link to="/" aria-label="pulsewatch">
          <Logo />
        </Link>
        <ButtonLink to={auth.isAuthenticated ? "/" : "/login"} size="sm">
          {auth.isAuthenticated ? "К журналу" : "Войти"}
        </ButtonLink>
      </div>
      <h1 className="page-title text-center sm:text-3xl">Тарифы</h1>
      <p className="mx-auto mt-2 max-w-xl text-center text-inkMuted">
        Платите за число мониторов и глубину проверок. Перейти на другой тариф можно в любой момент.
      </p>
      <div className="mx-auto mt-6 max-w-xl">
        <FormError message={error} />
      </div>
      {plans.isError ? (
        <ErrorPanel error={plans.error} onRetry={() => void plans.refetch()} />
      ) : null}
      <div className="mt-8 grid items-stretch gap-4 md:grid-cols-3">
        {plans.isPending
          ? [1, 2, 3].map((item) => <Skeleton key={item} className="h-[560px] rounded-lg" />)
          : ordered.map((plan) => {
              const featured = plan.code === "pro";
              const mine = plan.code === current;
              return (
                <section
                  key={plan.code}
                  aria-labelledby={`plan-${plan.code}`}
                  className={`surface relative flex h-full flex-col p-6 ${featured ? "border-accent/60 shadow-[0_0_0_1px_rgb(var(--accent)/0.35)]" : ""}`}
                >
                  {featured ? (
                    <span className="absolute -top-3 left-6 rounded-full bg-accent px-2.5 py-0.5 text-2xs font-semibold text-onAccent">
                      Рекомендуем
                    </span>
                  ) : null}
                  <h2 id={`plan-${plan.code}`} className="text-lg font-semibold">
                    {plan.name}
                  </h2>
                  <p className="mt-3 flex items-baseline gap-1.5">
                    <span className="text-3xl font-semibold tracking-[-0.03em]">
                      {plan.price_kop ? formatMoney(plan.price_kop) : "0 ₽"}
                    </span>
                    <span className="text-[13px] text-inkMuted">/ {plan.period_days} дней</span>
                  </p>
                  <ul className="mt-5 grid gap-1.5 text-[13px]">
                    {limits(plan).map((line) => (
                      <li key={line} className="font-medium">
                        {line}
                      </li>
                    ))}
                  </ul>
                  <ul
                    className="mt-5 grid flex-1 content-start gap-2 border-t border-line pt-5 text-[13px]"
                    aria-label={`Возможности ${plan.name}`}
                  >
                    {FEATURES.map(([feature, label]) => {
                      const included = Boolean(plan.features[feature]);
                      return (
                        <li
                          key={feature}
                          className={`flex items-start gap-2 ${included ? "" : "text-inkFaint line-through decoration-inkFaint/60"}`}
                        >
                          {included ? (
                            <Check
                              size={15}
                              className="mt-px shrink-0 text-accent"
                              aria-hidden="true"
                            />
                          ) : (
                            <Minus size={15} className="mt-px shrink-0" aria-hidden="true" />
                          )}
                          <span>{label}</span>
                          <span className="sr-only">{included ? ", входит" : ", не входит"}</span>
                        </li>
                      );
                    })}
                  </ul>
                  <Button
                    className="mt-6 w-full"
                    variant={featured ? "primary" : "secondary"}
                    disabled={mine}
                    loading={busy === plan.code}
                    onClick={() => void choose(plan.code)}
                  >
                    {mine
                      ? "Текущий тариф"
                      : plan.price_kop
                        ? `Перейти на ${plan.name}`
                        : auth.isAuthenticated
                          ? "Перейти на Free"
                          : "Начать бесплатно"}
                  </Button>
                </section>
              );
            })}
      </div>
      <p className="mt-6 text-center text-xs text-inkMuted">
        Оплата картой через ЮKassa. После отмены тариф действует до конца оплаченного периода.
      </p>
    </main>
  );
}

export function DevPaymentPage() {
  const navigate = useNavigate();
  const [path] = useState(() => {
    const value = sessionStorage.getItem("pw.checkout") ?? "";
    return /^\/billing\/dev\/[\w-]+\?signature=[a-f0-9]+$/.test(value) ? value : null;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <section className="panel mx-auto max-w-lg">
      <h1 className="section-title text-lg">Тестовая оплата</h1>
      <p className="mt-1 text-[13px] text-inkMuted">
        Режим разработки: платёж подтверждается без карты, подписка активируется сразу.
      </p>
      <div className="mt-4">
        <FormError message={error} />
      </div>
      {path ? (
        <Button
          className="mt-4"
          variant="primary"
          loading={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await apiRequest(path, {
                method: "POST",
                authenticated: false,
                responseType: "text",
              });
              sessionStorage.removeItem("pw.checkout");
              navigate("/billing/return", { replace: true });
            } catch (problem) {
              setError(errorMessage(problem));
            } finally {
              setBusy(false);
            }
          }}
        >
          Подтвердить тестовую оплату
        </Button>
      ) : (
        <p className="mt-4 text-[13px] text-inkMuted">
          Платёж не найден.{" "}
          <Link to="/pricing" className="underline">
            Выберите тариф
          </Link>
          .
        </p>
      )}
    </section>
  );
}

export function BillingReturnPage() {
  const subscription = useSubscription();
  const [attempts, setAttempts] = useState(0);
  const active = subscription.data?.status === "active" && subscription.data.plan.code !== "free";
  useEffect(() => {
    if (active || attempts > 10) return;
    const timer = window.setTimeout(() => {
      void subscription.refetch();
      setAttempts((value) => value + 1);
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [active, attempts, subscription]);
  return (
    <section className="panel mx-auto max-w-lg text-center" role="status">
      <h1 className="section-title text-lg">
        {active
          ? "Оплата прошла"
          : attempts > 10
            ? "Ждём подтверждение платежа"
            : "Проверяем платёж…"}
      </h1>
      <p className="mt-2 text-[13px] text-inkMuted">
        {active && subscription.data
          ? `Тариф ${subscription.data.plan.name} активен до ${formatDate(subscription.data.current_period_end)}.`
          : "Провайдер ещё не прислал подтверждение. Обычно это занимает несколько секунд."}
      </p>
      <ButtonLink to="/" variant="primary" className="mt-5">
        К журналу
      </ButtonLink>
    </section>
  );
}
