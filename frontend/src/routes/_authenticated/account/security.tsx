import { createFileRoute } from "@tanstack/react-router";
import { ChangePassword } from "@/components/security/change-password";
import { TotpPanel } from "@/components/security/totp-panel";
import { PasskeyPanel } from "@/components/security/passkey-panel";
import { Separator } from "@/components/ui/separator";

export const Route = createFileRoute("/_authenticated/account/security")({
  component: Security,
});

function Security() {
  return (
    <div className="space-y-8">
      <section>
        <h2 className="text-lg font-semibold mb-2">Password</h2>
        <ChangePassword />
      </section>
      <Separator />
      <section>
        <h2 className="text-lg font-semibold mb-2">Authenticator app (TOTP)</h2>
        <TotpPanel />
      </section>
      <Separator />
      <section>
        <h2 className="text-lg font-semibold mb-2">Passkeys</h2>
        <PasskeyPanel />
      </section>
    </div>
  );
}
