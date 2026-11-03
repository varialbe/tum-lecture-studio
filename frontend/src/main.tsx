import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "./index.css";
import { Layout } from "./components/Layout";
import { StoreProvider } from "./lib/store";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import Courses from "./pages/Courses";
import CourseDetail from "./pages/CourseDetail";
import Queue from "./pages/Queue";
import Transcripts from "./pages/Transcripts";
import TranscriptView from "./pages/TranscriptView";
import Models from "./pages/Models";

try {
  document.documentElement.dataset.theme = localStorage.getItem("theme") || "dark";
} catch {
  document.documentElement.dataset.theme = "dark";
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Dashboard /> },
      { path: "/login", element: <Login /> },
      { path: "/courses", element: <Courses /> },
      { path: "/courses/:semester/:slug", element: <CourseDetail /> },
      { path: "/queue", element: <Queue /> },
      { path: "/transcripts", element: <Transcripts /> },
      { path: "/transcripts/:id", element: <TranscriptView /> },
      { path: "/models", element: <Models /> },
      { path: "*", element: <Dashboard /> },
    ],
  },
]);

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StoreProvider>
      <RouterProvider router={router} />
    </StoreProvider>
  </StrictMode>,
);
