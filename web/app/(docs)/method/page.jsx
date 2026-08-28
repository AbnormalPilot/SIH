import PageHeader from "@/components/page-header.jsx";
import Method from "@/components/method.jsx";

export const metadata = {
  title: "Pipeline",
  description:
    "Collect, normalise, impute, aggregate: the four stages that turn fare quotes into an index value.",
};

export default function Page() {
  return (
    <>
      <PageHeader
        eyebrow="Pipeline"
        title="Scrape to index, in four stages."
        lede="Collect, normalise, impute, aggregate. Each stage is a separate module with its own tests, and each writes what it did to the ledger the next stage reads."
      />
      <Method />
    </>
  );
}
