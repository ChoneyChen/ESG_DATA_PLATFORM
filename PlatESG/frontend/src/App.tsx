import { useLocation } from "./navigation";
import PublishedDataApp from "./components/PublishedData";

export default function App() {
  const [params] = useLocation();
  return <PublishedDataApp params={params} />;
}
