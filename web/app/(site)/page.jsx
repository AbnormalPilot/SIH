import Overview from "@/components/overview.jsx";

export const metadata = {
  // The root page keeps the default title rather than the "%s · Pushpak"
  // template, which would read "Overview · Pushpak" on the front door.
  title: {
    absolute: "Pushpak · Real-time Airfare Price Index",
  },
  description:
    "Today's Pushpak Index value, the daily series it sits in, and what the ten-route basket covers.",
};

export default function Page() {
  return <Overview />;
}
