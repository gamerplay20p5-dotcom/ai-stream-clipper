import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import React, { useEffect } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError } from "./api";
import Layout from "./components/Layout";
import "./index.css";
import Clips from "./pages/Clips";
import Diagnostics from "./pages/Diagnostics";
import Director from "./pages/Director";
import Evaluation from "./pages/Evaluation";
import Publishing from "./pages/Publishing";
import Infrastructure from "./pages/Infrastructure";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import PatchNotes from "./pages/PatchNotes";
import Settings from "./pages/Settings";
import Storage from "./pages/Storage";
import Streamers from "./pages/Streamers";

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
    queryFn: () => api.get<{ email: string; role: string }>("/api/auth/me"),
    retry: false,
  });
  useEffect(() => {
    const onUnauth = () => qc.setQueryData(["me"], null);
    window.addEventListener("asc:unauthorized", onUnauth);
    return () => window.removeEventListener("asc:unauthorized", onUnauth);
  }, []);

  if (me.isLoading) return <div className="p-10 text-ink-muted">Carregando…</div>;
  const user = me.data;
  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login onDone={() => me.refetch()} />} />
      {user ? (
        <Route element={<Layout email={user.email} />}>
          <Route index element={<Overview />} />
          <Route path="cortes" element={<Clips />} />
          <Route path="cortes/:id" element={<Clips />} />
          <Route path="streamers" element={<Streamers />} />
          <Route path="ia" element={<Settings />} />
          <Route path="infra" element={<Infrastructure />} />
          <Route path="diagnostico" element={<Diagnostics />} />
          <Route path="avaliacao" element={<Evaluation />} />
          <Route path="diretor" element={<Director />} />
          <Route path="armazenamento" element={<Storage />} />
          <Route path="patch-notes" element={<PatchNotes />} />
          <Route path="publicacao" element={<Publishing />} />
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
