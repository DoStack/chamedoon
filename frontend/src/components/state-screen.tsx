import { type LucideIcon } from 'lucide-react'
import { type ReactNode } from 'react'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'

// Empty and error screens, per `empty` and `C.centered` in the design library.
export function StateBox({ icon: Icon, title, description, children, testId }: { icon: LucideIcon; title: string; description?: string; children?: ReactNode; testId?: string }) {
  return (
    <Empty data-testid={testId}>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Icon />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {description ? <EmptyDescription>{description}</EmptyDescription> : null}
      </EmptyHeader>
      {children ? <EmptyContent>{children}</EmptyContent> : null}
    </Empty>
  )
}

export function StateScreen(props: Parameters<typeof StateBox>[0]) {
  return (
    <div className="flex h-full flex-col items-center justify-center p-4 md:p-6">
      <StateBox {...props} />
    </div>
  )
}
