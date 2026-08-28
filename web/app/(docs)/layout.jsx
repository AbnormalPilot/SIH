import Pager from "@/components/pager.jsx";

/**
 * The documentation pages share the site's frame and add one thing: previous
 * and next across their reading order. The landing page is not part of that
 * order, so it does not get the pager.
 */
export default function DocsLayout({ children }) {
  return (
    <>
      {children}
      <Pager />
    </>
  );
}
