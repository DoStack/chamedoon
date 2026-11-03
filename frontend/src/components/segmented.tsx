import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { appDir } from '@/lib/locale'

// `segmented` in the design library. One choice among a few, applied on tap.
export function Segmented<T extends string>({ value, options, onChange, label, testId, disabled }: { value: T; options: Array<{ value: T; label: string }>; onChange: (next: T) => void; label: string; testId?: string; disabled?: boolean }) {
  return (
    <Tabs value={value} dir={appDir()} className="w-full" data-testid={testId} onValueChange={(next) => onChange(next as T)}>
      <TabsList aria-label={label} className="w-full">
        {options.map((option) => (
          <TabsTrigger key={option.value} value={option.value} disabled={disabled} className="min-w-0 flex-1">
            <span className="truncate">{option.label}</span>
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  )
}
