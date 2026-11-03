"""The first neighbourhoods of each launch metro, loaded once by migration 0027 and edited in the admin after that.

Coordinates are rough centres for one map pin per area (ADR-8), written from general knowledge, not from a survey.
Check them on a map before the map screen ships. Only Toronto's groups come from a design (F07#2).
"""

from __future__ import annotations

# (metro city, country, [(canonical, Persian name, group, lat, lng, aliases)]). Rank follows the order here.
SEED = [
    (
        "Toronto",
        "CA",
        [
            ("north york", "نورث یورک", "شمال", 43.7615, -79.4111, "نورت یورک,willowdale,ویلودیل,yonge and finch,yonge and sheppard,yonge and steeles,yonge and york mills,bayview and sheppard,bayview village,don mills,yorkdale,fairview mall,iranian plaza"),
            ("richmond hill", "ریچموند هیل", "شمال", 43.8828, -79.4403, "ریچمند هیل,oak ridges,yonge and major mackenzie,yonge and elgin mills,yonge and 16th ave,hillcrest"),
            ("downtown", "داون‌تاون", "مرکز", 43.6532, -79.3832, "داون تاون,داونتاون,union station,king st,queen st,harbourfront,yorkville,chinatown,kensington,spadina"),
            ("scarborough", "اسکاربرو", "شرق", 43.7731, -79.2578, "اسکاربورو,kennedy,mccowan,morningside,scarborough town centre"),
            ("vaughan", "وان", "شمال", 43.8361, -79.4983, "woodbridge,maple,concord,thornhill woods"),
            ("markham", "مارکهام", "شمال", 43.8561, -79.3370, "unionville"),
            ("thornhill", "تورنهیل", "شمال", 43.8100, -79.4200, "تورن هیل"),
            ("etobicoke", "اتوبیکوک", "غرب", 43.6205, -79.5132, "اتوبیکو,islington,kipling,royal york,the queensway,rexdale"),
            ("midtown", "میدتاون", "مرکز", 43.7040, -79.3980, "yonge and eglinton,davisville,forest hill"),
            ("east york", "ایست یورک", "مرکز", 43.6910, -79.3280, "danforth,leaside"),
            ("mississauga", "میسیساگا", "غرب", 43.5890, -79.6441, "میسی ساگا,square one,hurontario,erin mills,port credit,cooksville,meadowvale,heartland"),
            ("pickering", "پیکرینگ", "شرق", 43.8384, -79.0868, "ajax"),
            ("newmarket", "نیومارکت", "شمال", 44.0592, -79.4613, "aurora"),
            ("brampton", "برمپتون", "غرب", 43.7315, -79.7624, ""),
        ],
    ),
    (
        "Montreal",
        "CA",
        [
            ("cote des neiges", "کت د نژ", "", 45.4969, -73.6239, "کوت د نژ,cdn,ndg,notre dame de grace"),
            ("downtown", "مرکز شهر", "", 45.5017, -73.5673, "centre ville,ville marie"),
            ("plateau", "پلاتو", "", 45.5230, -73.5810, "plateau mont royal,mile end"),
            ("saint laurent", "سن لوران", "", 45.5070, -73.6880, "ville saint laurent"),
            ("verdun", "وردن", "", 45.4580, -73.5710, "lasalle"),
            ("westmount", "وست مانت", "", 45.4840, -73.5990, ""),
            ("laval", "لاوال", "", 45.6066, -73.7124, ""),
            ("longueuil", "لانگوی", "", 45.5312, -73.5181, ""),
            ("brossard", "بروسارد", "", 45.4580, -73.4650, "برسارد"),
            ("west island", "وست آیلند", "", 45.4600, -73.8200, "dollard des ormeaux,pierrefonds,kirkland,pointe claire"),
        ],
    ),
    (
        "Vancouver",
        "CA",
        [
            ("downtown", "مرکز ونکوور", "", 49.2827, -123.1207, "west end,yaletown,coal harbour"),
            ("north vancouver", "نورث ونکوور", "", 49.3200, -123.0724, "lonsdale"),
            ("west vancouver", "وست ونکوور", "", 49.3280, -123.1600, ""),
            ("burnaby", "برنابی", "", 49.2488, -122.9805, "metrotown,brentwood"),
            ("coquitlam", "کوکیتلام", "", 49.2838, -122.7932, "کوکویتلام,port moody,port coquitlam"),
            ("richmond", "ریچموند", "", 49.1666, -123.1336, ""),
            ("surrey", "سری", "", 49.1913, -122.8490, ""),
            ("new westminster", "نیو وست‌مینستر", "", 49.2057, -122.9110, ""),
            ("east vancouver", "شرق ونکوور", "", 49.2600, -123.0700, "kingsway,commercial drive"),
        ],
    ),
    (
        "Berlin",
        "DE",
        [
            ("mitte", "میته", "", 52.5200, 13.4050, "moabit"),
            ("wedding", "ودینگ", "", 52.5500, 13.3600, "gesundbrunnen"),
            ("neukolln", "نویکلن", "", 52.4810, 13.4350, "نویکولن,britz"),
            ("kreuzberg", "کرویتسبرگ", "", 52.4990, 13.4030, "friedrichshain"),
            ("charlottenburg", "شارلوتنبورگ", "", 52.5170, 13.2890, "wilmersdorf"),
            ("spandau", "اشپانداو", "", 52.5350, 13.2000, "اشپاندائو"),
            ("steglitz", "اشتگلیتس", "", 52.4560, 13.3220, "zehlendorf"),
            ("tempelhof", "تمپلهوف", "", 52.4660, 13.3850, "schoneberg"),
            ("pankow", "پانکو", "", 52.5690, 13.4010, "prenzlauer berg"),
            ("lichtenberg", "لیشتنبرگ", "", 52.5150, 13.4990, ""),
            ("marzahn", "مارتسان", "", 52.5450, 13.5650, "hellersdorf"),
            ("treptow", "ترپتو", "", 52.4400, 13.5700, "kopenick"),
            ("reinickendorf", "راینیکندورف", "", 52.5900, 13.3200, "tegel"),
        ],
    ),
    (
        "Hamburg",
        "DE",
        [
            ("mitte", "مرکز هامبورگ", "", 53.5511, 9.9937, "st pauli,hammerbrook,billstedt"),
            ("altona", "آلتونا", "", 53.5500, 9.9350, "ottensen,blankenese"),
            ("eimsbuttel", "آیمزبوتل", "", 53.5770, 9.9500, ""),
            ("nord", "شمال هامبورگ", "", 53.6000, 10.0200, "winterhude,eppendorf,langenhorn,لانگنهورن"),
            ("barmbek", "بارمبک", "", 53.5850, 10.0450, ""),
            ("wandsbek", "واندسبک", "", 53.5720, 10.0800, "bramfeld,rahlstedt"),
            ("bergedorf", "برگدورف", "", 53.4880, 10.2150, ""),
            ("harburg", "هاربورگ", "", 53.4600, 9.9800, ""),
            ("wilhelmsburg", "ویلهلمزبورگ", "", 53.4950, 10.0100, "ویلهلمسبورگ,veddel,وددل"),
        ],
    ),
    (
        "Cologne",
        "DE",
        [
            ("innenstadt", "مرکز کلن", "", 50.9375, 6.9603, "altstadt,deutz"),
            ("ehrenfeld", "ارنفلد", "", 50.9500, 6.9150, ""),
            ("nippes", "نیپس", "", 50.9650, 6.9500, ""),
            ("lindenthal", "لیندنتال", "", 50.9250, 6.9050, "sulz,klettenberg"),
            ("rodenkirchen", "رودنکیرشن", "", 50.8900, 6.9900, ""),
            ("kalk", "کالک", "", 50.9400, 7.0100, ""),
            ("porz", "پورتس", "", 50.8850, 7.0550, ""),
            ("mulheim", "مولهایم", "", 50.9650, 7.0100, ""),
            ("chorweiler", "کورفایلر", "", 51.0250, 6.9000, ""),
        ],
    ),
]
