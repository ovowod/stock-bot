import { createRoot } from "react-dom/client";
import App from "./App";
import { ToastProvider } from "./features/toast/Toasts";
import "./index.css";

// StrictMode는 개발 모드에서 화면 진입 시 데이터 요청을 두 번 보낸다. 키움은 같은 TR을
// 짧은 간격으로 두 번 부르면 호출 한도 오류(1700, 유량=1)를 돌려주므로 쓰지 않는다.
createRoot(document.getElementById("root")!).render(
  <ToastProvider>
    <App />
  </ToastProvider>,
);
