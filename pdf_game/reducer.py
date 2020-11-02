'''
An optimizer to reduce the GameViews by identifying the ones that will end up
being rendered exactly the same, including the links.
In practice, this only applies to Game Over death pages, and pages pointing to them.
'''
import os
from contextlib import contextmanager

import fpdf
try:  # Optional dependency:
    from tqdm import tqdm
except ImportError:
    tqdm = lambda _: _

from .assigner import assign_page_ids
from .perfs import disable_tracing, print_memory_stats
from .render import render_page


MIN_REMOVED_VIEWS_TO_GO_ON = 25


def reduce_views(game_views, multipass=True):
    print('Starting views reducer: 1st, assigning page IDs')
    pdf = fpdf.FPDF()  # a real instance is needed due to the calls to ._parsepng
    pass_number, total_views_removed = 1, 0
    game_views = assign_page_ids(game_views, assign_reverse_id=False)
    fingerprinted_pages = build_fingerprinted_pages(pdf, game_views)
    while True:
        if multipass:
            print(f'Pass {pass_number} - #views removed so far: {total_views_removed}')
        gv_per_page_fingerprint, filtered_fp_pages = {}, []
        print_memory_stats()
        # We need to assign page IDs in order to detect pages with identical links:
        for fp_page in tqdm(fingerprinted_pages, disable='NO_TQDM' in os.environ):
            existing_matching_gv = gv_per_page_fingerprint.get(fp_page.fingerprint)
            if existing_matching_gv:
                # gs = game_view.state
                # print('- reducer.removes:', f'{gs.coords}/{gs.facing} HP={gs.hp} round={gs.combat and gs.combat.round}')
                if fp_page.game_view.page_id == existing_matching_gv.page_id:
                    print(fp_page.game_view, existing_matching_gv)
                assert fp_page.game_view.page_id != existing_matching_gv.page_id, 'Prevent infinite loop'
                fp_page.game_view.page_id_from(existing_matching_gv)
                for incoming_fp_page in fp_page.incoming_pages:
                    incoming_fp_page.fingerprint = compute_fingerprint(pdf, incoming_fp_page.game_view)
            else:
                gv_per_page_fingerprint[fp_page.fingerprint] = fp_page.game_view
                filtered_fp_pages.append(fp_page)
        views_removed = len(fingerprinted_pages) - len(filtered_fp_pages)
        total_views_removed += views_removed
        pass_number += 1
        fingerprinted_pages = filtered_fp_pages
        if not views_removed or not multipass or views_removed < MIN_REMOVED_VIEWS_TO_GO_ON:  # Last condition avoid > 20 passes...
            break
    print(f'-{100*(total_views_removed)/len(game_views):0f}% of views were removed by the reducer')
    return [fp_page.game_view for fp_page in fingerprinted_pages]


def build_fingerprinted_pages(pdf, game_views):
    print('FingerprintedPages build step 1/2: initialization')
    fp_pages = []
    for game_view in tqdm(game_views, disable='NO_TQDM' in os.environ):
        fp_pages.append(FingerprintedPage(pdf, game_view))
    print('FingerprintedPages build step 2/2: setting .incoming_pages')
    fp_pages_per_page_id = {fp_page.game_view.page_id: fp_page for fp_page in fp_pages}
    for fp_page in tqdm(fp_pages, disable='NO_TQDM' in os.environ):
        for game_view in fp_page.game_view.actions.values():
            if game_view:
                fp_pages_per_page_id[game_view.page_id].incoming_pages.append(fp_page)
    return fp_pages


class FingerprintedPage:
    def __init__(self, pdf, game_view):
        self.game_view = game_view
        self.pdf = pdf
        self.fingerprint = compute_fingerprint(pdf, game_view)
        self.incoming_pages = []  # FingerprintedPages


def compute_fingerprint(pdf, game_view):
    fake_pdf = FakePdfRecorder(pdf)
    with disable_tracing():
        render_page(fake_pdf, game_view, render_victory_noop)
    return fake_pdf.get_fingerprint()


def render_victory_noop(*_): pass


class FakePdfRecorder:
    'Fake fpdf.FPDF class that must implement all the methods used during the pages rendering'
    def __init__(self, pdf):
        self.pdf = pdf
        self.images = pdf.images
        self._calls = []
        self._links = {}

    def add_font(self, family, style='', fname='', uni=False):
        pass

    def add_page(self):
        self._calls.append('add_page')

    def set_font(self, family, style='', size=0):
        self._calls.append(('set_font', family, style, size))

    def text(self, x, y, txt=''):
        self._calls.append(('text', x, y, txt))

    def set_text_color(self, r,g=-1,b=-1):
        self._calls.append(('set_text_color', r, g, b))

    def image(self, name, x=None, y=None, w=0, h=0, link=''):
        self._calls.append(('image', name, x, y, w, h, link))

    @contextmanager
    def rect_clip(self, x, y, w, h):
        self._calls.append(('rect_clip', x, y, w, h))
        yield

    @contextmanager
    def rotation(self, angle, x=None, y=None):
        self._calls.append(('rotation', angle, x, y))
        yield

    def add_link(self):
        return len(self._links) + 1

    def set_link(self, link, page=-1):
        self._links[link] = page

    def link(self, x, y, w, h, link, alt_text=''):
        page_or_url = link if isinstance(link, str) else self._links[link]
        self._calls.append(('link', x, y, w, h, page_or_url, alt_text))

    def _parsepng(self, filename):
        # pylint: disable=protected-access
        return self.pdf._parsepng(filename)

    def get_fingerprint(self):
        return hash(tuple(self._calls))
