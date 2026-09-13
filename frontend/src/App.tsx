import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Overview from "./pages/Overview";
import Placeholder from "./pages/Placeholder";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route
          path="ask"
          element={
            <Placeholder
              title="Ask"
              phase="Phase 4 (pipeline) and Phase 5 (frontend)"
              description="Submit a research question and follow the run stage by stage: sources, claims with stance, conflicts, consensus, critic verdict and the cited answer."
            />
          }
        />
        <Route
          path="runs"
          element={
            <Placeholder
              title="Runs"
              phase="Phase 5"
              description="Every run is persisted with its evidence, citation graph, tokens, cost and latency, and can be reopened here."
            />
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
