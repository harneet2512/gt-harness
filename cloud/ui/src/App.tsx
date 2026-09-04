import { Route, Routes } from "react-router-dom";
import Dashboard from "./components/Dashboard";
import Workspace from "./components/Workspace";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/sessions/:id" element={<Workspace />} />
    </Routes>
  );
}
