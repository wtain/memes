import asyncio
from collections import defaultdict

from sqlalchemy import select, exists
from sqlalchemy.orm import aliased
from sqlalchemy.sql.functions import count

from Storage.db import AsyncSessionLocal
from Storage.models import Image, OCRText


async def main():

    rules = {
        "Metallica": ['metallica', 'hetfield', 'lars', 'ride the lightning', 'kill \'em all', 'metallika'],
        "Iron Maiden": ['iron maiden'],
        "Tool": ['tool', 'pneuma', 'stinkfist'],
        "Bullet For My Valentine": ['bullet for my valentine', 'valentine', 'bfmv'],
        "Dimmu Borgir": ['dimmu', 'borgir'],
        "Slayer": ['slayer', 'kerry', 'king', 'tom', 'araya', 'reign in blood', 'raining blood', '0-0', '000', '0 0', 'angel of death'],
        "Creedence Clearwater Revival": ['credence', 'clearwater', 'ccr'],
        "Drowning Pool": ['drowning pool', 'let the bodies', 'hit the floor'],
        "Manowar": ['manowar'],
        "Children of Bodom": ['children of bodom', 'alexi', 'laiho'],
        "Megadeth": ['megadeth', 'mustaine', 'dave mustaine'],
        "Anthrax": ['anthrax'],
        "Red Hot Chili Peppers": ['red hot chili peppers', 'rhcp', 'anthony kiedis'],
        "Def Leppard": ['def leppard'],
        "Wintersun": ['wintersun', 'jari'],
        "Necrophagist": ['necrophagist'],
        "Death": ['death', 'shuldiner'],
        "AC/DC": ['ac/dc'],
        "Xavleg": ['xavleg'],
        "Evanescence": ['evanescence', 'wake me up', 'wake me up inside'],
        "Deicide": ['deicide', 'glen benton'],
        "Gojira": ['gojira', 'whale'],
        "Meshuggah": ['meshuggah'],
        "Led Zeppelin": ['led zeppelin'],
        "Sabaton": ['sabaton', 'for the grace', 'for the glory'],
        "Thin Lizzy": ['thin lizzy', 'the boys are back in town'],
        "Disturbed": ['disturbed', 'sickness'],
        "Lorna Shore": ['lorna', 'wil ramos', 'will ramos', 'snarling'],
        "Burzum": ['burzum', 'varg', 'burz', 'burn', 'church'],
        "Pantera": ['pantera', 'respect', 'walk', 'anselmo', 'dimebag', 'darrel'],
        "Danzig": ['glenn danzig', 'glen danzig', 'danzig'],
        "Bob Dylan": ['bob dylan', 'dylan'],
        "Lamb of God": ['lamb of god', 'redneck'],
        "Sanguisabogg": ['sanguisabogg'],
        "Nirvana": ['nirvana', 'kurt', 'teen\'s spirit'],
        "Dream Theater": ['dream theater', 'petrucci'],
        "Toto": ['toto', 'africa'],
        "Nickelback": ['nickelback', 'chad kroger'],
        "Alestorm": ['alestorm', 'pirate', 'plank'],
        "Pearl Jam": ['pearl'],
        "Soundgarden": ['soundgarden', 'blackhole', 'black hole'],
        "Knockedloose": ['knockedloose'],
        "Limb Bizkit": ['limp', 'bizkit', 'fred durst', 'durst'],
        "Static-X": ['static-x', 'wayne static'],
        "Alice in Chains": ['alice'],
        "Sepultura": ['sepultura', 'bloody roots'],
        "Soulfly": ['soulfly'],
        "Norther": ['norther'],
        "Kalmah": ['kalmah'],
        "Taylor Swift": ['swift'],
        "Britney Spears": ['britney'],
        "Aqua": ['aqua'],
        "Stratovarius": ['stratovarius'],
        "Thapsody of Fire": ['rhapsody of fire', 'luka turilli'],
        "Twisted Sister": ['twisted', 'i wanna rock'],
        "Steel Panther": ['panther', 'gloryhole', 'glory', 'hole'],
        "KISS": ['kiss', 'gene simmons'],
        "Opeth": ['mikael', 'akkerfeldt', 'opeth', 'blackwater'],
        "Dethklok": ['dethklok', 'nathan explosion', 'pretty metal', 'pretty brutal'],
        "Simon & Garfunkel": ['simon', 'garfunkel'],
        "Oasis": ['wonderwall'],
        "Napalm Death": ['napalm death', 'you suffer'],
        "Blink 182": ['blink 182', 'blink-182'],
        "Wind Rose": ['dwarf', 'wind rose', 'hole'],
        "Arch Enemy": ['arch enemy', 'alissa', 'white-gluz', 'gossow', 'angela', 'amott'],
        "Thrash": ['thrash'],
        "Black": ['black'],
        "Black Metal": ['black metal', 'trve', 'kvlt', 'corpsepaint', 'corpse paint'],
        "Cradle of Filth": ['cradle of filth', 'dani filth'],
        "Satanic": ['satanic'],
        "Power": ['power'],
        "Grunge": ['grunge'],
        "Heavy": ['heavy'],
        "Hard": ['hard'],
        "Rock": ['rock'],
        "Punk": ['punk'],
        "Saved Me": ['saved me'],
        "Mozart": ['mozart'],
        "Bach": ['bach'],
        "Beethoven": ['beethoven'],
        "Vivaldi": ['vivaldi'],
        "Worship": ['worship'],
        "Corpse": ['corpse'],
        "Metal": ['metal', 'metallist', 'metalico', 'metalhead'],
        "Six Feet Under": ['six feet under', 'sfu', 'chris barnes', 'eee'],
        "Doom Metal": ['doom', 'doom metal', 'doom fans'],
        "Supremacy": ['supremacy'],
        "Nu Metal": ['numetal', 'nu metal'],
        "Crowbar": ['crowbar'],
        "Eternal Tears of Sorrow": ['eternal tears of sorrow'],
        "Green Day": ['greenday', 'green day'],
        "My Chemical Romance": ['mcr', 'my chemical romance', 'black parade'],
        "Hardcore": ['hardcore'],
        "Deathcore": ['deathcore', 'china cymbal'],
        "Deathmetal": ['death metal', 'technical', 'slamming', 'brutal'],
        "Progressive": ['prog', 'progressive'],
        "Mudvayne": ['mudvayne', 'brbr', 'deng'],
        "Moshpits": ['mosh', 'pit'],
        "Blastbeats": ['brr', 'trr', 'blastbeats'],
        "Riffs": ['riff', 'caveman'],
        "Necrogoblikon": ['goblin'],
        "Korn": ['jonathan davis', 'korn', 'da boom na', 'freak on a leash'],
        "Guns'n'Roses": ['guns n roses', 'axl rose', 'slash', 'gnr'],
        "Papa Roach": ['papa roach', 'last resort'],
        "Smashing Pumpkins": ['smashing pumpkins'],
        "Torsofuck": ['torsofuck'],
        "The Black Dahlia Murder": ['black dahlia', 'tbdm'],
        "Motorhead": ['motorhead', 'ace of spaces', 'lemmy', 'kilmister'],
        "Judas Priest": ['judas priest', 'rob halford'],
        "Finntroll": ['trolls'],
        "System of a Down": ['system of a down', 'mezmerize', 'hypnotize', 'toxicity', 'terracotta', 'wake up', 'serj', 'tankian', 'daron', 'malakian', 'chop suey', 'chop soey', 'chop suy'],
        "Slipknot": ['slipknot', 'i push my', 'into my eyes', 'wait and bleed'],
        "Baby Metal": ['baby metal', 'babymetal', 'chocolate'],
        "Bryan Adams": ['bryan adams', 'summer of \'69'],
        "Mariah Carey": ['mariah carey'],
        "Gorgoroth": ['gorgoroth'],
        "Linkin Park": ['linkin'],
        "Paramore": ['paramore'],
        "Coldplay": ['coldplay'],
        "Deftones": ['deftones'],
        "Foo fighters": ['grohl', 'foo', 'confession to make'],
        "Necrophagist": ['necrophagist'],
        "Dying Fetus": ['dying fetus', 'drying fetus'],
        "Cannibal Corpse": ['cannibal'],
        "Dragonforce": ['dragon', 'herman lee'],
        "Infant Annihilator": ['infant'],
        "Beatles": ['beatles', 'lennon', 'yoko', 'abbey road'],
        "Venom": ['venom'],
        "Depeche Mode": ['depeche mode'],
        "Mayhem": ['mayhem', 'euronymous'],
        "Marduk": ['marduk'],
        "Black Sabbath": ['black sabbath', 'sabbath', 'zzy', 'ozzy', 'paranoid', 'iron man'],
        "Lynyrd Skynyrd": ['alabama', 'lynyrd', 'skynyrd'],
        "Elvis Presley": ['elvis', 'presley'],
        "Queen": ['queen', 'freddie mercury', 'mama'],
        "Gothic": ['goth'],
        "The Police": ['police', 'the police', 'sting', 'roxane'],
    }

    # todo: not to produce duplicates

    img = aliased(Image)
    ocr = aliased(OCRText)
    async with AsyncSessionLocal() as session:

        total_images = (await session.execute(
            select(count(Image.id))
        )).scalar_one()

        query = (
            select(
                # img, ocr,
                img.filename, ocr.text, ocr.confidence
            ).join(ocr, ocr.image_id == img.id)
        )
        result = await session.execute(query)
        images_matches = defaultdict(set)
        no_rules_count = 0
        for filename, text, confidence in result:
            if confidence < 0.4:
                continue
            for band_name in rules:
                for token in rules[band_name]:
                    if token in str.lower(text):
                        images_matches[filename].add(band_name)
            if filename not in images_matches:
                no_rules_count += 1
                print(text)

        stmt = (
            select(Image.filename)
            .where(
                ~exists().where(OCRText.image_id == Image.id)
            )
        )
        result = await session.execute(stmt)

        images_matched = 0
        no_ocr_count = len(result)

        print('-' * 20)
        print(f"Total images: {total_images}")
        print(f"Images matches: {len(images_matches)}")
        print(f"No rules: {no_rules_count}")
        print(f"No OCR: {no_ocr_count}")
        print(f"Images covered by rules: {images_matched}")

if __name__ == "__main__":
    asyncio.run(main())