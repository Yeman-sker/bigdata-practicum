/** Shimmer placeholders shown while panel data is in flight. */
export function SkeletonRows({ count = 4 }: { count?: number }) {
  return (
    <div className="skeleton-list" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <div
          className="skeleton-row"
          key={index}
          style={{ animationDelay: `${index * 90}ms` }}
        >
          <i />
          <span>
            <b />
            <b />
          </span>
          <em />
        </div>
      ))}
    </div>
  );
}

export function SkeletonChart() {
  return <div className="skeleton-chart" aria-hidden="true" />;
}
