import React from "react";
import { createRoot } from "react-dom/client";
import App from "./app/App";
import "./styles.css";

type BoundaryState = { error: Error | null };

class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  BoundaryState
> {
  state: BoundaryState = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page-heading">
        <h1>页面渲染失败</h1>
        <p>
          界面遇到未预期的数据形状，已保存的业务成果不受影响。刷新页面后可以继续；若重复出现，请记录下方信息。
        </p>
        <pre className="mono">{this.state.error.message}</pre>
        <button onClick={() => window.location.reload()}>刷新页面</button>
      </div>
    );
  }
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
