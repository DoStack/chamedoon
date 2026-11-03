import { X } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui/drawer'
import { useBack } from '@/lib/back'

const PHONE = '(pointer: coarse), (max-width: 767px)'

export function useIsPhone() {
  const [phone, setPhone] = useState(() => window.matchMedia(PHONE).matches)
  useEffect(() => {
    const media = window.matchMedia(PHONE)
    const apply = () => setPhone(media.matches)
    media.addEventListener('change', apply)
    return () => media.removeEventListener('change', apply)
  }, [])
  return phone
}

type Props = {
  open: boolean
  // Null: no close button, no scrim dismissal, and Back passes to the page (F25#5, F11#1).
  onClose: (() => void) | null
  title?: ReactNode
  label: string
  children?: ReactNode
  foot?: ReactNode
  testId?: string
}

// Bottom sheet on phone, centered dialog on desktop, per `C.sheet` in the design library.
export function AppSheet({ open, onClose, title, label, children, foot, testId }: Props) {
  const phone = useIsPhone()
  useBack(open && onClose ? onClose : null)
  const dismiss = (next: boolean) => {
    if (!next) onClose?.()
  }
  const head =
    title || onClose ? (
      <div className="flex shrink-0 items-center justify-between gap-2">
        <div className="min-w-0 text-[18px] leading-[26px] font-extrabold">{title}</div>
        {onClose ? (
          <Button variant="ghost" size="icon" aria-label="بستن" onClick={onClose}>
            <X className="size-5" />
          </Button>
        ) : null}
      </div>
    ) : null
  const body = (
    <>
      {head}
      {children ? <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">{children}</div> : null}
      {foot ? <div className="shrink-0">{foot}</div> : null}
    </>
  )
  if (phone) {
    return (
      <Drawer open={open} onOpenChange={dismiss} dismissible={Boolean(onClose)} repositionInputs={false}>
        <DrawerContent
          dir="rtl"
          data-testid={testId}
          className="gap-2.5 bg-background px-4 pt-0 pb-[calc(1rem+var(--safe-b))] data-[vaul-drawer-direction=bottom]:max-h-[calc(var(--app-h)-var(--safe-t))]"
          aria-describedby={undefined}
        >
          <DrawerTitle className="sr-only">{label}</DrawerTitle>
          {body}
        </DrawerContent>
      </Drawer>
    )
  }
  return (
    <Dialog open={open} onOpenChange={dismiss}>
      <DialogContent
        dir="rtl"
        data-testid={testId}
        showCloseButton={false}
        className="flex max-h-[85vh] flex-col gap-3 rounded-2xl bg-background p-5 sm:max-w-[520px]"
        aria-describedby={undefined}
        onInteractOutside={(event) => {
          if (!onClose) event.preventDefault()
        }}
        onEscapeKeyDown={(event) => {
          if (!onClose) event.preventDefault()
        }}
      >
        <DialogTitle className="sr-only">{label}</DialogTitle>
        {body}
      </DialogContent>
    </Dialog>
  )
}
