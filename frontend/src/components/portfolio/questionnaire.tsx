import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  QUESTIONNAIRE,
  stateToSearch,
  walk,
  type ExploreSearch,
} from "@/lib/portfolio/questionnaire";

const MAX_QUERY_LEN = 280; // mirrors the rank serializer's cap

export function Questionnaire({
  onDone,
}: {
  onDone: (search: ExploreSearch) => void;
}) {
  const [answers, setAnswers] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const { state, next } = useMemo(
    () => walk(QUESTIONNAIRE, answers),
    [answers],
  );

  if (next) {
    return (
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{next.prompt}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {next.options.map((o) => (
            <Button
              key={o.id}
              variant="outline"
              className="justify-start"
              onClick={() => setAnswers((a) => [...a, o.id])}
            >
              {o.label}
            </Button>
          ))}
        </CardContent>
      </Card>
    );
  }

  // Finale: optional free text ("i feel lucky" skips straight to the result).
  if (state.lucky) {
    onDone(stateToSearch(state));
    return null;
  }
  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle>Anything specific you're curious about?</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Input
          value={query}
          maxLength={MAX_QUERY_LEN}
          placeholder="e.g. building things with local AI models"
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="flex gap-2">
          <Button onClick={() => onDone(stateToSearch(state, query))}>
            Show me
          </Button>
          <Button variant="ghost" onClick={() => onDone(stateToSearch(state))}>
            Skip
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
