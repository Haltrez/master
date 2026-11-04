import { CommandK } from "@/components/command-k";
import { Nav } from "@/components/nav";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <Nav />
      <CommandK />
      <main className="mx-auto max-w-7xl px-4 py-5 sm:py-6">{children}</main>
    </>
  );
}
