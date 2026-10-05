import { ChevronRight, Menu, Search, ShieldAlert, Sparkles, Trophy, Wallet, X, type LucideIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { EnvironmentValue } from "./api";
import { ENVIRONMENTS, findEnvironment, loadEnvironment, saveEnvironment } from "./environments";
import { AccountPage } from "./features/account/AccountPage";
import { RankingPage } from "./features/ranking/RankingPage";
import { StockSearchPage } from "./features/search/StockSearchPage";

interface Feature {
  id: string;
  label: string;
  icon: LucideIcon;
  render: (environment: EnvironmentValue) => ReactNode;
}

interface FeatureGroup {
  title: string;
  features: Feature[];
}

// 기능을 추가할 때는 알맞은 분류에 항목 하나만 더한다. 새 분류도 같은 방식으로 더한다.
const GROUPS: FeatureGroup[] = [
  {
    title: "자산",
    features: [
      {
        id: "account",
        label: "계좌 확인",
        icon: Wallet,
        // key로 투자 환경을 묶어, 환경을 바꾸면 이전 환경의 데이터와 진행 중인 요청을 버린다.
        render: (environment) => <AccountPage key={environment} environment={environment} />,
      },
    ],
  },
  {
    title: "시세",
    features: [
      {
        id: "stock-search",
        label: "종목 검색",
        icon: Search,
        render: (environment) => <StockSearchPage key={environment} environment={environment} />,
      },
      {
        id: "ranking",
        label: "순위",
        icon: Trophy,
        render: (environment) => <RankingPage key={environment} environment={environment} />,
      },
    ],
  },
];

const FEATURES = GROUPS.flatMap((group) => group.features);

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
      <header className="sticky top-0 z-30 bg-surface/90 backdrop-blur">
        <div className="flex h-14 items-center gap-2.5 px-4 md:h-16 md:px-6">
          <button
            type="button"
            className="-ml-1.5 rounded-xl p-2 text-sub hover:bg-canvas md:hidden"
            aria-label="메뉴 열기"
            onClick={() => setDrawerOpen(true)}
          >
            <Menu className="size-5" />
          </button>
          <span className="hidden size-8 items-center justify-center rounded-xl bg-brand-600 text-white md:flex">
            <Sparkles className="size-4" aria-hidden />
          </span>
          <h1 className="truncate text-[17px] font-bold md:text-lg">Stock Bot</h1>
          {current.isReal && (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-real-soft px-2.5 py-1 text-xs font-bold text-real">
              <ShieldAlert className="size-3.5" aria-hidden />
              실전
            </span>
          )}
          <div className="ml-auto hidden md:block">
            <EnvironmentSwitch value={environment} onChange={setEnvironment} />
          </div>
        </div>
        <div className="px-4 pb-3 md:hidden">
          <EnvironmentSwitch value={environment} onChange={setEnvironment} />
        </div>
        {current.isReal && (
          <p className="flex items-center gap-2 bg-real-soft px-4 py-2 text-[13px] font-semibold text-real md:px-6">
            <ShieldAlert className="size-4 shrink-0" aria-hidden />
            실제 계좌와 실제 자금을 다루는 실전투자 환경입니다.
          </p>
        )}
      </header>

      <div className="flex flex-1">
        <aside className="hidden w-60 shrink-0 bg-surface md:block">
          <Navigation activeId={feature.id} onSelect={setFeatureId} />
        </aside>

        {drawerOpen && (
          <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="메뉴">
            <button
              type="button"
              className="absolute inset-0 bg-ink/40"
              aria-label="메뉴 닫기"
              onClick={() => setDrawerOpen(false)}
            />
            <div className="absolute inset-y-0 left-0 flex w-72 max-w-[80%] flex-col rounded-r-3xl bg-surface shadow-2xl">
              <div className="flex h-14 items-center justify-between px-5">
                <span className="text-[17px] font-bold">메뉴</span>
                <button
                  type="button"
                  className="rounded-xl p-2 text-sub hover:bg-canvas"
                  aria-label="메뉴 닫기"
                  onClick={() => setDrawerOpen(false)}
                >
                  <X className="size-5" />
                </button>
              </div>
              <Navigation
                activeId={feature.id}
                onSelect={(id) => {
                  setFeatureId(id);
                  setDrawerOpen(false);
                }}
              />
            </div>
          </div>
        )}

        <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">{feature.render(environment)}</main>
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
      className="grid grid-cols-4 gap-1 rounded-2xl bg-canvas p-1 md:flex"
    >
      {ENVIRONMENTS.map((env) => {
        const active = env.value === value;
        const activeColor = env.isReal ? "text-real" : "text-brand-600";
        return (
          <button
            key={env.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(env.value)}
            className={`rounded-xl px-2 py-2 text-[13px] font-semibold whitespace-nowrap transition md:px-4 md:text-sm ${
              active ? `bg-surface shadow-[0_1px_3px_rgba(0,0,0,0.08)] ${activeColor}` : "text-muted hover:text-sub"
            }`}
          >
            {env.label}
          </button>
        );
      })}
    </div>
  );
}

function Navigation({ activeId, onSelect }: { activeId: string; onSelect: (id: string) => void }) {
  return (
    <nav className="flex flex-col gap-5 p-3 pt-4" aria-label="기능">
      {GROUPS.map((group) => (
        <div key={group.title} role="group" aria-label={group.title} className="flex flex-col gap-1">
          <p className="px-3.5 pb-1 text-xs font-semibold tracking-wide text-muted">{group.title}</p>
          {group.features.map(({ id, label, icon: Icon }) => {
            const active = id === activeId;
            return (
              <button
                key={id}
                type="button"
                aria-current={active ? "page" : undefined}
                onClick={() => onSelect(id)}
                className={`flex items-center gap-3 rounded-2xl px-3.5 py-3 text-[15px] font-semibold transition ${
                  active ? "bg-brand-50 text-brand-700" : "text-sub hover:bg-canvas"
                }`}
              >
                <Icon className="size-5" aria-hidden />
                <span className="flex-1 text-left">{label}</span>
                {active && <ChevronRight className="size-4" aria-hidden />}
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
