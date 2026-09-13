import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Ask from "./pages/Ask";
import Overview from "./pages/Overview";
import RunPage from "./pages/RunPage";
import Runs from "./pages/Runs";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="ask" element={<Ask />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/:id" element={<RunPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
