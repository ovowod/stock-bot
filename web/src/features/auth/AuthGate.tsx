import { LockKeyhole, Sparkles } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, checkSession, login, onUnauthorized } from "../../api";

type AuthState = { status: "checking" } | { status: "anonymous"; notice: string | null } | { status: "authenticated" };

const EXPIRED = "로그인이 만료되었습니다. 다시 로그인하세요.";

/** 로그인 세션이 있을 때만 children을 그린다. 없으면 비밀번호 입력 화면을 보여준다. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "checking" });

  useEffect(() => {
    let active = true;
    checkSession().then(
      (ok) => active && setState(ok ? { status: "authenticated" } : { status: "anonymous", notice: null }),
      (error) =>
        active && setState({ status: "anonymous", notice: error instanceof ApiError ? error.message : null }),
    );
    const stop = onUnauthorized(() => setState({ status: "anonymous", notice: EXPIRED }));
    return () => {
      active = false;
      stop();
    };
  }, []);

  if (state.status === "checking") return null;
  if (state.status === "anonymous") {
    return <LoginPage notice={state.notice} onLoggedIn={() => setState({ status: "authenticated" })} />;
  }
  return children;
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
