import { render, screen, waitFor } from '@testing-library/react'
import { MemesDuplicatesList } from './MemesDuplicatesList'
import { makeMockApi } from '../test/mockApi'
import type { Meme } from '../types/generated/all'

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: { data: unknown[]; itemContent: (index: number, item: unknown) => React.ReactNode }) => (
    <div>
      {props.data.map((item, i) => (
        <div key={i}>{props.itemContent(i, item)}</div>
      ))}
    </div>
  ),
}))

function clusterMeme(id: string, clusterId: number): Meme {
  return { id, imageUrl: `/images/${id}.jpg`, text: [], tags: [], clusterId }
}

describe('MemesDuplicatesList', () => {
  it('calls iterateDuplicates on mount with no cursor by default', async () => {
    const api = makeMockApi()
    render(<MemesDuplicatesList memesApi={api} />)
    await waitFor(() => {
      expect(api.iterateDuplicates).toHaveBeenCalledWith(40, undefined, 0.2)
    })
  })

  it('starts from the URL-provided initialCursor', async () => {
    const api = makeMockApi()
    render(<MemesDuplicatesList memesApi={api} initialCursor="deep-link" />)
    await waitFor(() => {
      expect(api.iterateDuplicates).toHaveBeenCalledWith(40, "deep-link", 0.2)
    })
  })

  it('groups same-cluster members into one row and renders them together', async () => {
    const api = makeMockApi({
      iterateDuplicates: vi.fn().mockResolvedValue({
        items: [clusterMeme('a', 1), clusterMeme('b', 1), clusterMeme('c', 2)],
        hasNext: false,
      }),
    })
    render(<MemesDuplicatesList memesApi={api} />)
    await waitFor(() => {
      expect(screen.getByRole('img', { name: 'a' })).toBeInTheDocument()
      expect(screen.getByRole('img', { name: 'b' })).toBeInTheDocument()
      expect(screen.getByRole('img', { name: 'c' })).toBeInTheDocument()
    })
  })

  it('shows "Nothing to show" when there are no clusters', async () => {
    render(<MemesDuplicatesList memesApi={makeMockApi()} />)
    await waitFor(() => {
      expect(screen.getByText('Nothing to show')).toBeInTheDocument()
    })
  })
})
