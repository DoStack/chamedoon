import { Bookmark } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { toggle, useIsSaved } from '@/lib/saved'
import { cn } from '@/lib/utils'

// The icon on a card and a listing. Filled and in the primary color when marked.
export function BookmarkButton({ id, className }: { id: number; className?: string }) {
  const saved = useIsSaved(id)
  return (
    <Button
      variant="outline"
      size="icon"
      aria-label={saved ? 'برداشتن نشان' : 'نشان کردن'}
      aria-pressed={saved}
      data-testid="bookmark"
      className={cn(saved && 'text-primary hover:text-primary [&_svg]:fill-current', className)}
      onClick={() => toggle(id)}
    >
      <Bookmark className="size-5" />
    </Button>
  )
}

// The full-width form in the contact sheets (F11#8, #17).
export function BookmarkAction({ id, variant }: { id: number; variant?: 'default' | 'outline' }) {
  const saved = useIsSaved(id)
  return (
    <Button variant={variant} className="w-full" data-testid="bookmark-action" onClick={() => toggle(id)}>
      <Bookmark className={cn(saved && 'fill-current')} />
      {saved ? 'برداشتن نشان' : 'نشان کردن'}
    </Button>
  )
}
