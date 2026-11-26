import { createFileRoute } from "@tanstack/react-router";
import { ChangePassword } from "@/components/security/change-password";
import { TotpPanel } from "@/components/security/totp-panel";
import { PasskeyPanel } from "@/components/security/passkey-panel";
import { Separator } from "@/components/ui/separator";
import { SectionTitle } from "@/components/heading";

export const Route = createFileRoute("/_authenticated/account/security")({
  component: Security,
});

function Security() {
  return (
    <div className="space-y-8">
      <section>
        <SectionTitle className="mb-2">Password</SectionTitle>
        <ChangePassword />
      </section>
      <Separator />
      <section>
        <SectionTitle className="mb-2">Authenticator app (TOTP)</SectionTitle>
        <TotpPanel />
      </section>
      <Separator />
      <section>
        <SectionTitle className="mb-2">Passkeys</SectionTitle>
        <PasskeyPanel />
      </section>
    </div>
  );
}
