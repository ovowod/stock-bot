import { createContext, useContext, useEffect } from "react";

export const LEAVE_MESSAGE = "저장하지 않은 변경이 사라집니다. 이동할까요?";

/** 지금 화면에 저장하지 않은 변경이 있는지 앱 껍데기에 알린다. */
export const ReportUnsavedChanges = createContext<(dirty: boolean) => void>(() => {});

/** 화면이 사라지면 변경도 함께 사라지므로 "없음"으로 돌려놓는다. */
export function useReportUnsavedChanges(dirty: boolean) {
  const report = useContext(ReportUnsavedChanges);
  useEffect(() => {
    report(dirty);
    return () => report(false);
  }, [dirty, report]);
}
