import { CommandK } from "@/components/command-k";
import { Nav } from "@/components/nav";
import { VERSION } from "@/lib/version";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <Nav />
      <CommandK />
      <main className="mx-auto max-w-7xl px-4 py-5 sm:py-6">{children}</main>
      <footer className="mx-auto max-w-7xl px-4 pb-6 text-xs text-slate-400" data-testid="version">Version {VERSION}</footer>
    </>
  );
}
