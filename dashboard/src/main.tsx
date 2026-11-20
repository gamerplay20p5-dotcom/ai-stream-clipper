import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import React, { useEffect } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError, type Me } from "./api";
import { Skeleton } from "./components/ui";
import Layout from "./components/Layout";
import "./index.css";
import Clips from "./pages/Clips";
import Diagnostics from "./pages/Diagnostics";
import Director from "./pages/Director";
import Evaluation from "./pages/Evaluation";
import Publishing from "./pages/Publishing";
import Infrastructure from "./pages/Infrastructure";
import LibraryApp from "./pages/Library";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import PatchNotes from "./pages/PatchNotes";
import People from "./pages/People";
import References from "./pages/References";
import Settings from "./pages/Settings";
import Storage from "./pages/Storage";
import Streamers from "./pages/Streamers";
import { t } from "./i18n";

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (n, err) => !(err instanceof ApiError && err.status < 500) && n < 2,
      refetchOnWindowFocus: true,
      staleTime: 3_000,
    },
  },
});

function App() {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<Me>("/api/auth/me"),
    retry: false,
  });
  useEffect(() => {
    const onUnauth = () => qc.setQueryData(["me"], null);
    window.addEventListener("asc:unauthorized", onUnauth);
    return () => window.removeEventListener("asc:unauthorized", onUnauth);
  }, []);

  if (me.isLoading) return <main className="mx-auto max-w-md p-6"><Skeleton label={t("Verificando sessão")} /></main>;
  const user = me.data;
  // External accounts only ever get their library: no staff navigation, no staff requests.
  // This is presentation only; every API route is authorized by the backend.
  if (user && !(user.staff ?? user.role === "admin")) {
    return (
      <Routes>
        <Route path="/biblioteca" element={<LibraryApp email={user.email} />} />
        <Route path="*" element={<Navigate to="/biblioteca" replace />} />
      </Routes>
    );
  }
  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login onDone={() => me.refetch()} />} />
      {user ? (
        <Route element={<Layout email={user.email} />}>
          <Route index element={<Overview />} />
          <Route path="cortes/:id?" element={<Clips />} />
          <Route path="streamers" element={<Streamers />} />
          <Route path="ia" element={<Settings />} />
          <Route path="infra" element={<Infrastructure />} />
          <Route path="diagnostico" element={<Diagnostics />} />
          <Route path="avaliacao" element={<Evaluation />} />
          <Route path="diretor" element={<Director />} />
          <Route path="armazenamento" element={<Storage />} />
          <Route path="patch-notes" element={<PatchNotes />} />
          <Route path="referencias" element={<References />} />
          <Route path="publicacao" element={<Publishing />} />
          <Route path="pessoas" element={<People />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      ) : (
        <Route path="*" element={<Navigate to="/login" replace />} />
      )}
    </Routes>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
