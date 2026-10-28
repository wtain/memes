import type { RecutProposalDetail } from "../../recut/types"

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="text-sm font-medium">{value}</dd>
    </div>
  )
}

export default function ProposalInfo({ detail }: { detail: RecutProposalDetail }) {
  const { box } = detail
  return (
    <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      <Field label="Confidence" value={detail.confidence === null ? "not scored" : detail.confidence.toFixed(2)} />
      <Field label="Box (x, y, w x h)" value={`${box.x}, ${box.y}, ${box.w} x ${box.h}`} />
      <Field label="Detector" value={`${detail.detector} ${detail.detectorVersion}`} />
      <Field label="State" value={detail.state} />
    </dl>
  )
}
