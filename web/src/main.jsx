import React from "react";
import ReactDOM from "react-dom/client";

// Self-hosted variable fonts. No render-blocking Google Fonts link.
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";

import "./index.css";
import App from "./App.jsx";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
