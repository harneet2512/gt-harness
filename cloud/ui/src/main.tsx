import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { announceBuild } from "./build";
import { refreshPalette } from "./palette";
import { applyTheme, loadTheme } from "./theme";
import "./index.css";

announceBuild();
/* Before the first paint: the theme is an attribute on <html>, and the
   canvas reads its colours off the same custom properties. */
applyTheme(loadTheme());
refreshPalette();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/* Opt in to the v7 behaviours now: they are the defaults there, and
        without the flags every page logs two deprecation warnings. */}
    <BrowserRouter
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
