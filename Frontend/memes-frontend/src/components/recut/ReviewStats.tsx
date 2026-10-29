import type { RecutStats, RecutStatsBand } from "../../recut/types"

function rate(band: RecutStatsBand): string {
  return band.approveRate === null ? "-" : `${Math.round(band.approveRate * 100)}%`
}

function Row({ band }: { band: RecutStatsBand }) {
  return (
    <tr>
      <th scope="row" className="py-1 pr-4 text-left font-normal">{band.label}</th>
      <td className="py-1 pr-4 text-right">{band.waiting}</td>
      <td className="py-1 pr-4 text-right">{band.approved}</td>
      <td className="py-1 pr-4 text-right">{band.rejected}</td>
      <td className="py-1 text-right">{rate(band)}</td>
    </tr>
  )
}

export default function ReviewStats({ stats }: { stats: RecutStats }) {
  const shown = stats.bands.filter((band) => band.waiting + band.approved + band.rejected > 0)
  return (
    <section aria-labelledby="recut-review-so-far">
      <h2 id="recut-review-so-far" className="mb-2 text-lg font-semibold">Review so far</h2>
      <table className="text-sm">
        <thead>
          <tr>
            <th scope="col" className="pr-4 text-left font-medium">Confidence</th>
            <th scope="col" className="pr-4 text-right font-medium">Waiting</th>
            <th scope="col" className="pr-4 text-right font-medium">Approved</th>
            <th scope="col" className="pr-4 text-right font-medium">Rejected</th>
            <th scope="col" className="text-right font-medium">Approve rate</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((band) => <Row key={band.label} band={band} />)}
          <Row band={stats.total} />
        </tbody>
      </table>
      <p className="mt-2 text-xs text-gray-500">
        Approve rate is stricter than the gold &quot;as is or slightly off&quot; precision: a slightly-off box can still
        be rejected, so the two are not comparable one to one.
      </p>
    </section>
  )
}
