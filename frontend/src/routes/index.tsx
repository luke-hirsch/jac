import { createFileRoute } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
export const Route = createFileRoute("/")({
  component: Home,
});

function Home() {
  return (
    <div className="p-8 space-y-4">
      <h1 className="text-2xl font-bold">lukehirsch</h1>
      <Button>hello</Button>
    </div>
  );
}
