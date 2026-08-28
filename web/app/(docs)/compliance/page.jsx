import PageHeader from "@/components/page-header.jsx";
import Compliance from "@/components/compliance.jsx";

export const metadata = {
  title: "Compliance",
  description:
    "The robots.txt gate, the twelve fare sources it was run against, and the nine it refuses.",
};

export default function Page() {
  return (
    <>
      <PageHeader
        eyebrow="What scraping actually returns"
        title="Nine of twelve fare sources refuse automated collection."
        lede="The gate checks robots.txt before it opens a socket, and it fails closed. This page is the audit it produced, not a summary of one."
      />
      <Compliance />
    </>
  );
}
