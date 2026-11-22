// Call-to-action targets. Bot start parameters follow backend/home/deeplink.py (letters, digits, _ and -; ≤ 64 chars).
import { HOME_BOT, COURIER_BOT, APP_HOME, APP_COURIER, tme, type CityMeta } from './site';

const SRC = 'src-site';
const cc = (c: CityMeta) => c.country.toLowerCase();

/** Web app list for one city (same filters the Mini App reads). */
export const webCity = (c: CityMeta) => `${APP_HOME}list/?country=${cc(c)}&city=${c.slug}`;
/** Open the housing bot on one city's listings. */
export const botCity = (c: CityMeta) => `${tme(HOME_BOT)}?start=home_${cc(c)}_${c.slug}_${SRC}`;
/** Ask the housing bot for an alert on one city. */
export const alertCity = (c: CityMeta) => `${tme(HOME_BOT)}?start=alert_home_${cc(c)}_${c.slug}`;
/** Post a listing (Mini App post screen through the bot). */
export const postListing = () => `${tme(HOME_BOT)}?start=home_post`;
export const channelCity = (c: CityMeta) => tme(c.channel);
export const courierWeb = () => APP_COURIER;
export const courierBot = () => tme(COURIER_BOT);
