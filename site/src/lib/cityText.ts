import { cityBySlug, type Lang } from './site';
import { cityStats, headlineType, medianOf, safeMedian, updatedDay, typesWithData, MIN_N } from './stats';
import { money, num, typeLabel, dateParts, dateFa } from './format';

export function answerSentence(slug: string, lang: Lang, type = headlineType(slug)) {
  const c = cityBySlug(slug);
  const s = cityStats(slug);
  const m = s.medians_30d.find((x) => x.type === type);
  const med = safeMedian(m);
  const d = dateParts(updatedDay);
  if (lang === 'fa') {
    if (med == null) return `برای ${typeLabel(type)} در ${c.fa} در ۳۰ روز منتهی به ${d.greg} کمتر از ${num(MIN_N)} آگهی قیمت‌دار داشتیم؛ برای همین میانه‌ای اعلام نمی‌کنیم.`;
    return `میانهٔ اجارهٔ درخواستی ${typeLabel(type)} در ${c.fa} در ۳۰ روز منتهی به ${d.greg} (${d.shamsi}): ${money(med, c.currency)} در ماه (چمدون، ${num(m!.n)} آگهی).`;
  }
  if (med == null) return `Fewer than ${MIN_N} priced listings for ${typeLabel(type, 'en').toLowerCase()}s in ${c.en} in the 30 days to ${d.en}, so no median is published.`;
  return `Median asking rent for a ${typeLabel(type, 'en').toLowerCase()} in ${c.en} in the 30 days to ${d.en}: ${money(med, c.currency, 'en')} per month (Chamedoon, n=${m!.n} listings).`;
}

export function cityFaq(slug: string, lang: Lang) {
  const c = cityBySlug(slug);
  const s = cityStats(slug);
  const out: { q: string; a: string }[] = [];
  const types = typesWithData(slug);
  const updated = lang === 'fa' ? dateFa(updatedDay) : dateParts(updatedDay).en;
  for (const t of ['room', 'apartment', 'studio', 'basement'].filter((x) => types.includes(x)).slice(0, 3)) {
    out.push(
      lang === 'fa'
        ? { q: `اجارهٔ ${typeLabel(t)} در ${c.fa} چقدر است؟`, a: answerSentence(slug, 'fa', t) + ` آخرین به‌روزرسانی: ${updated}.` }
        : { q: `How much is a ${typeLabel(t, 'en').toLowerCase()} in ${c.en}?`, a: answerSentence(slug, 'en', t) + ` Updated ${updated}.` },
    );
  }
  out.push(
    lang === 'fa'
      ? { q: `هر هفته چند آگهی خانهٔ فارسی در ${c.fa} منتشر می‌شود؟`, a: `در ۷ روز منتهی به ${updated}، ${num(s.new_7d)} آگهی تازهٔ اجاره در ${c.fa} به چمدون رسید و در ۳۰ روز گذشته ${num(s.new_30d)} آگهی.` }
      : { q: `How many Persian rental listings appear in ${c.en} each week?`, a: `In the 7 days to ${updated}, Chamedoon received ${num(s.new_7d, 'en')} new rental listings in ${c.en}, and ${num(s.new_30d, 'en')} in the last 30 days.` },
  );
  out.push(
    lang === 'fa'
      ? { q: `چطور آگهی‌های خانهٔ ${c.fa} را ببینم؟`, a: `در ربات چمدونِ خانه (<span dir="ltr">@chamedoon_home_bot</span>) شهر ${c.fa} را انتخاب کنید، یا عضو کانال <span dir="ltr">@${c.channel}</span> شوید. برای آگهی‌های تازه هم گوش‌به‌زنگ بسازید.` }
      : { q: `How can I see rental listings in ${c.en}?`, a: `Open the Chamedoon Home bot (<span dir="ltr">@chamedoon_home_bot</span>) and pick ${c.en}, or join the <span dir="ltr">@${c.channel}</span> channel. Set an alert to hear about new listings.` },
  );
  return out;
}

export const plainText = (html: string) => html.replace(/<[^>]+>/g, '');
