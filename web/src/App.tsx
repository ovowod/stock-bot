import { ChevronRight, LineChart, Menu, ShieldAlert, Wallet, X, type LucideIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { EnvironmentValue } from "./api";
import { ENVIRONMENTS, findEnvironment, loadEnvironment, saveEnvironment } from "./environments";
import { AccountPage } from "./features/account/AccountPage";

interface Feature {
  id: string;
  label: string;
  icon: LucideIcon;
  render: (environment: EnvironmentValue) => ReactNode;
}

// 기능을 추가할 때는 이 목록에 항목 하나만 더한다.
const FEATURES: Feature[] = [
  {
    id: "account",
    label: "계좌 확인",
    icon: Wallet,
    // key로 투자 환경을 묶어, 환경을 바꾸면 이전 환경의 데이터와 진행 중인 요청을 버린다.
    render: (environment) => <AccountPage key={environment} environment={environment} />,
  },
];

export default function App() {
  const [environment, setEnvironment] = useState<EnvironmentValue>(loadEnvironment);
  const [featureId, setFeatureId] = useState(FEATURES[0].id);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const current = findEnvironment(environment);
  const feature = FEATURES.find((f) => f.id === featureId) ?? FEATURES[0];

  useEffect(() => saveEnvironment(environment), [environment]);

  useEffect(() => {
    if (!drawerOpen) return;
    const close = (event: KeyboardEvent) => event.key === "Escape" && setDrawerOpen(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [drawerOpen]);

  return (
    <div className="flex min-h-dvh flex-col">
      <header
        className={`sticky top-0 z-30 border-b bg-surface ${
          current.isReal ? "border-real/30 shadow-[inset_0_3px_0_var(--color-real)]" : "border-line"
        }`}
      >
        <div className="flex h-14 items-center gap-3 px-4 md:h-16 md:px-6">
          <button
            type="button"
            className="-ml-1 rounded-md p-2 text-muted hover:bg-canvas md:hidden"
            aria-label="메뉴 열기"
            onClick={() => setDrawerOpen(true)}
          >
            <Menu className="size-5" />
          </button>
          <LineChart className="hidden size-5 text-brand-600 md:block" aria-hidden />
          <h1 className="truncate text-base font-semibold tracking-tight md:text-lg">
            주식 자동매매 대시보드
          </h1>
          {current.isReal && (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-real px-2 py-0.5 text-xs font-semibold text-white">
              <ShieldAlert className="size-3.5" aria-hidden />
              실전
            </span>
          )}
          <div className="ml-auto hidden md:block">
            <EnvironmentSwitch value={environment} onChange={setEnvironment} />
          </div>
        </div>
        <div className="px-3 pb-3 md:hidden">
          <EnvironmentSwitch value={environment} onChange={setEnvironment} />
        </div>
        {current.isReal && (
          <p className="flex items-center gap-2 bg-real-soft px-4 py-1.5 text-xs font-medium text-real md:px-6">
            <ShieldAlert className="size-3.5 shrink-0" aria-hidden />
            실제 계좌와 실제 자금을 다루는 실전투자 환경입니다.
          </p>
        )}
      </header>

      <div className="flex flex-1">
        <aside className="hidden w-60 shrink-0 border-r border-line bg-surface md:block">
          <Navigation features={FEATURES} activeId={feature.id} onSelect={setFeatureId} />
        </aside>

        {drawerOpen && (
          <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="메뉴">
            <button
              type="button"
              className="absolute inset-0 bg-ink/40"
              aria-label="메뉴 닫기"
              onClick={() => setDrawerOpen(false)}
            />
            <div className="absolute inset-y-0 left-0 flex w-72 max-w-[80%] flex-col bg-surface shadow-xl">
              <div className="flex h-14 items-center justify-between border-b border-line px-4">
                <span className="font-semibold">메뉴</span>
                <button
                  type="button"
                  className="rounded-md p-2 text-muted hover:bg-canvas"
                  aria-label="메뉴 닫기"
                  onClick={() => setDrawerOpen(false)}
                >
                  <X className="size-5" />
                </button>
              </div>
              <Navigation
                features={FEATURES}
                activeId={feature.id}
                onSelect={(id) => {
                  setFeatureId(id);
                  setDrawerOpen(false);
                }}
              />
            </div>
          </div>
        )}

        <main className="min-w-0 flex-1 px-4 py-5 md:px-8 md:py-8">
          {feature.render(environment)}
        </main>
      </div>
    </div>
  );
}

function EnvironmentSwitch({
  value,
  onChange,
}: {
  value: EnvironmentValue;
  onChange: (value: EnvironmentValue) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="투자 환경"
      className="grid grid-cols-4 gap-1 rounded-xl border border-line bg-canvas p-1 md:flex"
    >
      {ENVIRONMENTS.map((env) => {
        const active = env.value === value;
        const activeColor = env.isReal ? "bg-real text-white" : "bg-brand-700 text-white";
        return (
          <button
            key={env.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(env.value)}
            className={`whitespace-nowrap rounded-lg px-2 py-1.5 text-[13px] font-medium transition-colors md:px-4 md:text-sm ${
              active ? `${activeColor} shadow-sm` : "text-muted hover:bg-surface hover:text-ink"
            }`}
          >
            {env.label}
          </button>
        );
      })}
    </div>
  );
}

function Navigation({
  features,
  activeId,
  onSelect,
}: {
  features: Feature[];
  activeId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <nav className="flex flex-col gap-1 p-3" aria-label="기능">
      {features.map(({ id, label, icon: Icon }) => {
        const active = id === activeId;
        return (
          <button
            key={id}
            type="button"
            aria-current={active ? "page" : undefined}
            onClick={() => onSelect(id)}
            className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 text-sm font-medium transition-colors ${
              active
                ? "border-brand-100 bg-brand-50 text-brand-900"
                : "border-transparent text-muted hover:bg-canvas hover:text-ink"
            }`}
          >
            <Icon className="size-4.5" aria-hidden />
            <span className="flex-1 text-left">{label}</span>
            {active && <ChevronRight className="size-4" aria-hidden />}
          </button>
        );
      })}
    </nav>
  );
}
