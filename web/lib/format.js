export const inr = (n) =>
  n == null ? "n/a" : `₹${Math.round(n).toLocaleString("en-IN")}`;

export const num = (n, d = 2) =>
  n == null
    ? "n/a"
    : Number(n).toLocaleString("en-IN", {
        minimumFractionDigits: d,
        maximumFractionDigits: d,
      });

export const pct = (n, d = 1) => (n == null ? "n/a" : `${(n * 100).toFixed(d)}%`);

export const compactPax = (n) =>
  n == null ? "n/a" : `${(n / 1_000_000).toFixed(2)}M`;
