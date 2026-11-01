import { useEffect, useRef, useState } from "react";
import { ApiError, fetchSlTp, saveSlTp, type EnvironmentValue, type SlTpDraft, type SlTpSettings } from "../../api";
import { useReportUnsavedChanges } from "../../unsavedChanges";
import { useToast } from "../toast/Toasts";

export type LoadState =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "ready"; saved: SlTpSettings };

const EMPTY_DRAFT: SlTpDraft = { excluded: [], sessions: [] };

const draftOf = (settings: SlTpSettings): SlTpDraft => ({
  excluded: settings.excluded,
  sessions: settings.sessions,
});

const asApiError = (error: unknown) =>
  error instanceof ApiError ? error : new ApiError("unknown", "알 수 없는 오류입니다.", null);

/**
 * 한 투자 환경의 SL / TP 설정. 서버에 저장된 값과 편집 중인 값을 따로 두고,
 * 저장 버튼을 눌러야 편집 중인 값을 서버에 보낸다(환경이 바뀌면 컴포넌트가 새로 만들어진다).
 */
export function useSlTp(environment: EnvironmentValue) {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [draft, setDraft] = useState<SlTpDraft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const toast = useToast();

  const load = async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setState({ status: "loading" });
    try {
      const settings = await fetchSlTp(environment, current.signal);
      if (current.signal.aborted) return;
      setState({ status: "ready", saved: settings });
      setDraft(draftOf(settings));
    } catch (error) {
      if (current.signal.aborted) return;
      setState({ status: "error", error: asApiError(error) });
    }
  };

  useEffect(() => {
    void load();
    return () => controller.current?.abort();
    // 환경마다 컴포넌트를 새로 만들므로 처음 한 번만 불러온다.
  }, []);

  const saved = state.status === "ready" ? state.saved : null;
  const dirty = saved !== null && JSON.stringify(draft) !== JSON.stringify(draftOf(saved));
  useReportUnsavedChanges(dirty);

  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    try {
      const settings = await saveSlTp(environment, draft);
      setState({ status: "ready", saved: settings });
      setDraft(draftOf(settings));
      toast({ tone: "success", title: "SL / TP 설정을 저장했습니다" });
    } catch (error) {
      const apiError = asApiError(error);
      toast({
        tone: "error",
        title: "SL / TP 설정을 저장하지 못했습니다",
        body: apiError.message,
        meta: apiError.requestId ? [`요청 ID ${apiError.requestId}`] : undefined,
      });
    } finally {
      setSaving(false);
    }
  };

  return { state, draft, setDraft, dirty, saving, save, retry: () => void load() };
}
