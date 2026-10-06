import { LockKeyhole, Sparkles } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, checkSession, login, logout, onUnauthorized } from "../../api";
import { useClearToasts } from "../toast/Toasts";

type AuthState = { status: "checking" } | { status: "anonymous"; notice: string | null } | { status: "authenticated" };

const EXPIRED = "로그인이 만료되었습니다. 다시 로그인하세요.";
const ORDER_EXPIRED = "로그인이 만료되어 주문하지 않았습니다. 다시 로그인하세요.";

const Logout = createContext<() => void>(() => {});

/** 이 브라우저의 로그인 세션을 끝내고 로그인 화면으로 간다. */
export function useLogout() {
  return useContext(Logout);
}

/** 페이지가 새로고침으로 열렸는지. 그 밖(새 탭, 주소 입력, 링크, 뒤로가기)은 새 접속으로 본다. */
function openedByReload(): boolean {
  const [entry] = performance.getEntriesByType("navigation") as PerformanceNavigationTiming[];
  return entry?.type === "reload";
}

/**
 * 로그인 세션이 있을 때만 children을 그린다. 없으면 비밀번호 입력 화면을 보여준다.
 * 새로고침이 아닌 방법으로 열리면 로그인 세션을 끝내고 비밀번호를 다시 묻는다.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "checking" });
  const clearToasts = useClearToasts();

  // 서버 로그아웃 응답이 쿠키를 지우므로, 그 응답을 받은 뒤에 로그인 화면을 보여준다.
  // 먼저 보여주면 그사이 새로 받은 로그인 쿠키를 늦게 온 로그아웃 응답이 지울 수 있다.
  const endSession = useCallback(
    async (notice: string | null, callServer: boolean) => {
      clearToasts();
      setState({ status: "checking" });
      if (callServer) await logout();
      setState({ status: "anonymous", notice });
    },
    [clearToasts],
  );

  useEffect(() => {
    let active = true;
    if (openedByReload()) {
      checkSession().then(
        (ok) => active && setState(ok ? { status: "authenticated" } : { status: "anonymous", notice: null }),
        (error) =>
          active && setState({ status: "anonymous", notice: error instanceof ApiError ? error.message : null }),
      );
    } else {
      void endSession(null, true);
    }
    // 서버가 이미 세션이 없다고 했으므로 로그아웃 요청은 보내지 않는다.
    const stop = onUnauthorized((source) => void endSession(source === "order" ? ORDER_EXPIRED : EXPIRED, false));
    // 브라우저가 저장해 둔 페이지를 뒤로가기로 그대로 보여준 경우도 새 접속이다.
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) void endSession(null, true);
    };
    window.addEventListener("pageshow", onPageShow);
    return () => {
      active = false;
      stop();
      window.removeEventListener("pageshow", onPageShow);
    };
  }, [endSession]);

  const signOut = useCallback(() => void endSession(null, true), [endSession]);

  if (state.status === "checking") return null;
  if (state.status === "anonymous") {
    return <LoginPage notice={state.notice} onLoggedIn={() => setState({ status: "authenticated" })} />;
  }
  return <Logout.Provider value={signOut}>{children}</Logout.Provider>;
}

function LoginPage({ notice, onLoggedIn }: { notice: string | null; onLoggedIn: () => void }) {
  const [password, setPassword] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (sending || !password) return;
    setSending(true);
    setError(null);
    try {
      await login(password);
      onLoggedIn();
    } catch (failure) {
      const apiError = failure instanceof ApiError ? failure : null;
      setError(
        apiError?.kind === "invalid_password"
          ? "비밀번호가 올바르지 않습니다."
          : (apiError?.message ?? "로그인하지 못했습니다."),
      );
      setPassword("");
      setSending(false);
    }
  };

  const message = error ?? notice;
  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <form
        onSubmit={submit}
        className="flex w-full max-w-sm flex-col gap-5 rounded-3xl bg-surface p-6 shadow-[0_8px_24px_rgba(0,0,0,0.06)] md:p-8"
      >
        <div className="flex items-center gap-2.5">
          <span className="flex size-9 items-center justify-center rounded-xl bg-brand-600 text-white">
            <Sparkles className="size-4" aria-hidden />
          </span>
          <h1 className="text-lg font-bold">Stock Bot</h1>
        </div>
        <label className="flex flex-col gap-2">
          <span className="text-sm font-semibold text-sub">비밀번호</span>
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="rounded-2xl bg-canvas px-4 py-3 text-[15px] outline-none focus:ring-2 focus:ring-brand-500"
          />
        </label>
        {message && (
          <p role="alert" className="text-sm font-semibold text-real">
            {message}
          </p>
        )}
        <button
          type="submit"
          disabled={sending || !password}
          className="flex items-center justify-center gap-2 rounded-2xl bg-brand-600 px-4 py-3 text-[15px] font-bold text-white transition hover:bg-brand-700 disabled:opacity-50"
        >
          <LockKeyhole className="size-4" aria-hidden />
          {sending ? "확인 중…" : "로그인"}
        </button>
      </form>
    </main>
  );
}
