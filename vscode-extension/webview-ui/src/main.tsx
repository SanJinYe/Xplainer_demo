import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import { WebviewStateProvider } from "./context/WebviewStateContext";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
        <WebviewStateProvider>
            <App />
        </WebviewStateProvider>
    </React.StrictMode>,
);
