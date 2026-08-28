import PageHeader from "@/components/page-header.jsx";
import Formula from "@/components/formula.jsx";

export const metadata = {
  title: "Index formula",
  description:
    "The fixed-basket Laspeyres arithmetic behind every published value, decomposed term by term.",
};

export default function Page() {
  return (
    <>
      <PageHeader
        eyebrow="Index formula"
        title="Any published number can be rebuilt by hand."
        lede="The index is a weighted sum of price relatives, so every value decomposes into fifty terms that add back to it exactly. The API returns that decomposition on request."
      />
      <Formula />
    </>
  );
}
