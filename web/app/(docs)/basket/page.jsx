import PageHeader from "@/components/page-header.jsx";
import BasketView from "@/components/basket-view.jsx";

export const metadata = {
  title: "Basket and weights",
  description:
    "Ten city pairs and five booking windows, weighted by DGCA passenger share rather than by hand.",
};

export default function Page() {
  return (
    <>
      <PageHeader
        eyebrow="Basket and weights"
        title="The basket is not a guess."
        lede="Ten city pairs, five booking windows, fifty cells. Every weight is a passenger share taken from the DGCA domestic city-pair release and recomputed by script."
      />
      <BasketView />
    </>
  );
}
