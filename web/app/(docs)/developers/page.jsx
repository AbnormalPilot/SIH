import PageHeader from "@/components/page-header.jsx";
import ApiSection from "@/components/api-section.jsx";
import CtaPanel from "@/components/cta-panel.jsx";

export const metadata = {
  title: "API reference",
  description:
    "Seven read-only endpoints, and the provenance fields that ship with every index value.",
};

export default function Page() {
  return (
    <>
      <PageHeader
        eyebrow="For downstream consumers"
        title="Provenance ships with every number."
        lede="Seven read-only endpoints over the same database the dashboard reads. Every response carries the shares that say how much of it was observed."
      />
      <ApiSection />
      <CtaPanel />
    </>
  );
}
