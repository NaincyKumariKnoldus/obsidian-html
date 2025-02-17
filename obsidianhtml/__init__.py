import sys                  # commandline arguments
import os                   #
import shutil               # used to remove a non-empty directory, copy files
import uuid
import re                   # regex string finding/replacing
from pathlib import Path    # 
import markdown             # convert markdown to html
import yaml
import urllib.parse         # convert link characters like %
import frontmatter
import json

from .MarkdownPage import MarkdownPage
from .MarkdownLink import MarkdownLink
from .lib import DuplicateFileNameInRoot, GetObsidianFilePath, OpenIncludedFile, ExportStaticFiles, image_suffixes
from .PicknickBasket import PicknickBasket

# Open source files in the package
import importlib.resources as pkg_resources
import importlib.util
from . import src 

def recurseObisidianToMarkdown(page_path_str, pb):
    '''This functions converts an obsidian note to a markdown file and calls itself on any local note links it finds in the page.'''

    # Unpack picknick basket so we don't have to type too much.
    paths = pb.paths        # Paths of interest, such as the output and input folders
    files = pb.files        # Hashtable of all files found in the obsidian vault
    config = pb.config

    # Convert path string to Path and do a double check
    page_path = Path(page_path_str).resolve()
    if page_path.exists() == False:
        return
    if page_path.suffix != '.md':
        return

    # Convert note to markdown
    # ------------------------------------------------------------------
    # Create an object that handles a lot of the logic of parsing the page paths, content, etc
    md = MarkdownPage(page_path, paths['obsidian_folder'], files)

    # The bulk of the conversion process happens here
    md.ConvertObsidianPageToMarkdownPage(paths['md_folder'], paths['obsidian_entrypoint'])

    # The frontmatter was stripped from the obsidian note prior to conversion
    # Add yaml frontmatter back in
    md.page = (frontmatter.dumps(frontmatter.Post("", **md.metadata))) + '\n' + md.page

    # Save file
    # ------------------------------------------------------------------
    # Create folder if necessary
    md.dst_path.parent.mkdir(parents=True, exist_ok=True)

    # Write markdown to file
    with open(md.dst_path, 'w', encoding="utf-8") as f:
        f.write(md.page)

    # Recurse for every link in the current page
    # ------------------------------------------------------------------
    for l in md.links:
        link = GetObsidianFilePath(l, files)
        if link[1] == False or link[1]['processed'] == True:
            continue
        link_path = link[0]

        # Mark the file as processed so that it will not be processed again at a later stage
        files[link_path]['processed'] = True         

        # Convert the note that is linked to
        if config['toggles']['verbose_printout']:
            print(f"converting {files[link_path]['fullpath']} (parent {page_path})")

        recurseObisidianToMarkdown(files[link_path]['fullpath'], pb)

def ConvertMarkdownPageToHtmlPage(page_path_str, pb, backlinkNode=None):
    '''This functions converts a markdown page to an html file and calls itself on any local markdown links it finds in the page.'''
    
    # Unpack picknick basket so we don't have to type too much.
    paths = pb.paths                    # Paths of interest, such as the output and input folders
    files = pb.files                    # Hashtable of all files found in the obsidian vault
    html_template = pb.html_template    # Built-in or user-provided html template
    config = pb.config

    # Convert path string to Path and do a double check
    page_path = Path(page_path_str).resolve()
    if page_path.exists() == False:
        return
    if page_path.suffix != '.md':
        return

    # Load contents
    # ------------------------------------------------------------------
    # Create an object that handles a lot of the logic of parsing the page paths, content, etc
    md = MarkdownPage(page_path, paths['md_folder'], files)
    md.SetDestinationPath(paths['html_output_folder'], paths['md_entrypoint'])

    # [1] Replace code blocks with placeholders so they aren't altered
    # They will be restored at the end
    # ------------------------------------------------------------------
    md.StripCodeSections()     

    # Get all local markdown links. 
    # ------------------------------------------------------------------
    # This is any string in between '](' and  ')'
    proper_links = re.findall("(?<=\]\().+?(?=\))", md.page)
    for l in proper_links:
        # Init link
        link = MarkdownLink(l, page_path, paths['md_folder'], url_unquote=True, relative_path_md = config['toggles']['relative_path_md'])

        # Don't process in the following cases
        if link.isValid == False or link.isExternal == True: 
            continue

        # [12] Copy non md files over wholesale, then we're done for that kind of file
        if link.suffix != '.md' and link.suffix not in image_suffixes:
            paths['html_output_folder'].joinpath(link.rel_src_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(link.src_path, paths['html_output_folder'].joinpath(link.rel_src_path))
            continue

        # [13] Link to a custom 404 page when linked to a not-created note
        if link.url.split('/')[-1] == 'not_created.md':
            new_link = '](/not_created.html)'
        else:
            if link.rel_src_path_posix not in files.keys():
                continue

            md.links.append(link.rel_src_path_posix)

            # [11.1] Rewrite .md links to .html (when the link is to a file in our root folder)
            query_part = ''
            if link.query != '':
                query_part = link.query_delimiter + link.query 
            new_link = f']({config["html_url_prefix"]}/{link.rel_src_path_posix[:-3]}.html{query_part})'
            
        # Update link
        safe_link = re.escape(']('+l+')')
        md.page = re.sub(safe_link, new_link, md.page)

    # [4] Handle local image links (copy them over to output)
    # ------------------------------------------------------------------
    for link in re.findall("(?<=\!\[\]\()(.*?)(?=\))", md.page):
        l = urllib.parse.unquote(link)
        full_link_path = page_path.parent.joinpath(l).resolve()
        rel_path = full_link_path.relative_to(paths['md_folder'])

        # Only handle local image files (images located in the root folder)
        # Doublecheck, who knows what some weird '../../folder/..' does...
        if rel_path.as_posix() not in files.keys():
            if config['toggles']['warn_on_skipped_image']:
                warnings.warn(f"Image {str(full_link_path)} treated as external and not imported in html")
            continue

        # Copy src to dst
        dst_path = paths['html_output_folder'].joinpath(rel_path)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(full_link_path, dst_path)

        # [11.2] Adjust image link in page to new dst folder (when the link is to a file in our root folder)
        new_link = '![]('+urllib.parse.quote(rel_path.as_posix())+')'
        safe_link = re.escape('![](/'+link+')')
        md.page = re.sub(safe_link, new_link, md.page)
   

    # [1] Restore codeblocks/-lines
    # ------------------------------------------------------------------
    md.RestoreCodeSections()

    # [11] Convert markdown to html
    # ------------------------------------------------------------------
    extension_configs = {
    'codehilite ': {
        'linenums': True
    }}
    html_body = markdown.markdown(md.page, extensions=['extra', 'codehilite', 'toc', 'md_mermaid'], extension_configs=extension_configs)

    # HTML Tweaks
    # ------------------------------------------------------------------
    # [14] Tag external links with a class so they can be decorated differently
    for l in re.findall(r'(?<=\<a href=")([^"]*)', html_body):
        if l == '':
            continue
        if l[0] == '/':
            # Internal link, skip
            continue

        new_str = f"<a href=\"{l}\" class=\"external-link\""
        safe_str = f"<a href=\"{l}\""
        html_body = html_body.replace(safe_str, new_str)

    # [15] Tag not created links with a class so they can be decorated differently
    html_body = html_body.replace('<a href="/not_created.html">', '<a href="/not_created.html" class="nonexistent-link">')

    # Graph view integrations
    # ------------------------------------------------------------------
    # The nodelist will result in graph.json, which may have uses beyond the graph view

    # [17] Add self to nodelist
    node = pb.network_tree.NewNode()
    
    # Use filename as node id, unless 'graph_name' is set in the yaml frontmatter
    node['id'] = str(md.rel_dst_path).split('/')[-1].replace('.md', '')
    if 'graph_name' in md.metadata.keys():
        node['id'] = md.metadata['graph_name']

    # Url is used so you can open the note/node by clicking on it
    node['url'] = f'{config["html_url_prefix"]}/{str(md.rel_dst_path)[:-3]}.html'
    pb.network_tree.AddNode(node)

    # Backlinks are set so when recursing, the links (edges) can be determined
    if backlinkNode is not None:
        link = pb.network_tree.NewLink()
        link['source'] = backlinkNode['id']
        link['target'] = node['id']
        pb.network_tree.AddLink(link)
    
    backlinkNode = node

    # [17] Add in graph code to template (via {content})
    # This shows the "Show Graph" button, and adds the js code to handle showing the graph
    if config['toggles']['features']['build_graph']:
        graph_template = OpenIncludedFile('graph_template.html')
        html_body += "\n" + graph_template.replace('{id}', str(uuid.uuid4()).replace('-','')).replace('{pinnedNode}', node['id']) + "\n"

    # [16] Wrap body html in valid html structure from template
    # ------------------------------------------------------------------
    html = html_template\
        .replace('{title}', config['site_name'])\
        .replace('{html_url_prefix}', config['html_url_prefix'])\
        .replace('{dynamic_includes}', pb.dynamic_inclusions)\
        .replace('{content}', html_body)

    # Save file
    # ------------------------------------------------------------------
    md.dst_path.parent.mkdir(parents=True, exist_ok=True)   
    html_dst_path_posix = md.dst_path.as_posix()[:-3] + '.html' 

    md.AddToTagtree(pb.tagtree, md.dst_path.relative_to(paths['html_output_folder']).as_posix()[:-3] + '.html')

    # Write html
    with open(html_dst_path_posix, 'w', encoding="utf-8") as f:
        f.write(html)

    # > Done with this markdown page!

    # Recurse for every link in the current page
    # ------------------------------------------------------------------
    for l in md.links:
        # these are of type rel_path_posix
        link_path = l
        
        # Skip non-existent links, non-markdown links, and links that have been processed already
        if link_path not in files.keys():
            continue
        if files[link_path]['processed'] == True:
            continue
        if files[link_path]['fullpath'][-3:] != '.md':
            continue        

        # Mark the file as processed so that it will not be processed again at a later stage
        files[link_path]['processed'] = True

        # Convert the note that is linked to
        if config['toggles']['verbose_printout']:
            print("html: converting ", files[link_path]['fullpath'], " (parent ", md.src_path, ")")

        ConvertMarkdownPageToHtmlPage(files[link_path]['fullpath'], pb, backlinkNode)

def recurseTagList(tagtree, tagpath, pb, level):
    '''This function creates the folder `tags` in the html_output_folder, and a filestructure in that so you can navigate the tags.'''

    # Get relevant paths
    # ---------------------------------------------------------
    html_url_prefix = pb.config['html_url_prefix']
    tag_dst_path = pb.paths['html_output_folder'].joinpath(f'{tagpath}index.html').resolve()
    tag_dst_path_posix = tag_dst_path.as_posix()
    rel_dst_path_as_posix = tag_dst_path.relative_to(pb.paths['html_output_folder']).as_posix()

    # Compile markdown from tagtree
    # ---------------------------------------------------------
    md = ''
    # Handle subtags
    if len(tagtree['subtags'].keys()) > 0:
        if level == 0:
            md += '# Tags\n'
        else:
            md += '# Subtags\n'

        for key in tagtree['subtags'].keys():
            # Point of recursion
            rel_key_path_as_posix = recurseTagList(tagtree['subtags'][key], tagpath + key + '/', pb, level+1)
            md += f'- [{key}](/{rel_key_path_as_posix})' + '\n'

    # Handle notes
    if len(tagtree['notes']) > 0:
        md += '\n# Notes\n'
        for note in tagtree['notes']:
            md += f'- [{note.replace(".html", "")}]({html_url_prefix}/{note})\n'

    # Compile html
    html_body = markdown.markdown(md, extensions=['extra', 'codehilite', 'toc', 'md_mermaid'])

    html = pb.html_template.replace('{title}', pb.config['site_name'])\
        .replace('{html_url_prefix}', pb.config['html_url_prefix'])\
        .replace('{dynamic_includes}', '<link rel="stylesheet" href="/98682199-5ac9-448c-afc8-23ab7359a91b-static/taglist.css" />')\
        .replace('{content}', html_body)

    # Write file
    tag_dst_path.parent.mkdir(parents=True, exist_ok=True)   
    with open(tag_dst_path_posix, 'w', encoding="utf-8") as f:
        f.write(html) 

    # Return link of this page, to be used by caller for building its page
    return rel_dst_path_as_posix


def main():
    # Show help text
    # ---------------------------------------------------------
    if '-h' in sys.argv or len(sys.argv) < 3:
        print('[Obsidian-html]')
        print('- Add -i </path/to/input.yml> to provide config')
        print('- Add -v for verbose output')
        print('- Add -h to get helptext')
        print('- Add -eht <target/path/file.name> to export the html template.')
        exit()

    # Export packaged html template so users can edit it and then use their custom template
    # ---------------------------------------------------------
    export_html_template_target_path = None
    for i, v in enumerate(sys.argv):
        if v == '-eht':
            if len(sys.argv) < (i + 2):
                raise Exception("No output path given.\n Use obsidianhtml -eht /target/path/to/template.html to provide input.")
                exit(1)
            export_html_template_target_path = Path(sys.argv[i+1]).resolve()
            export_html_template_target_path.parent.mkdir(parents=True, exist_ok=True)
            html = OpenIncludedFile('template.html')
            with open (export_html_template_target_path, 'w', encoding="utf-8") as t:
                t.write(html)
            print(f"Exported html template to {str(export_html_template_target_path)}.")
            exit(0)

    # Load input yaml
    # ---------------------------------------------------------
    input_yml_path_str = ''
    for i, v in enumerate(sys.argv):
        if v == '-i':
            input_yml_path_str = sys.argv[i+1]
            break

    if input_yml_path_str == '':
        raise Exception("No yaml input given.\n Use obsidianhtml -i /path/to/config.yml to provide input.")
        exit(1)

    with open(input_yml_path_str, 'rb') as f:
        conf = yaml.load(f.read(), Loader=yaml.SafeLoader) 

    # Overwrite conf
    for i, v in enumerate(sys.argv):
        if v == '-v':
            conf['toggles']['verbose_printout'] = True

    # Set defaults
    set_build_graph = False 
    if 'features' not in conf['toggles']:
        conf['toggles']['features'] = {}
        set_build_graph = True
    else:
        if 'build_graph' not in conf['toggles']['features']:
            set_build_graph = True
    if 'process_all' not in conf['toggles']:
        conf['toggles']['process_all'] = False

    if set_build_graph:
        conf['toggles']['features']['build_graph'] = True


    # Set Paths
    # ---------------------------------------------------------
    paths = {
        'obsidian_folder': Path(conf['obsidian_folder_path_str']).resolve(),
        'md_folder': Path(conf['md_folder_path_str']).resolve(),
        'obsidian_entrypoint': Path(conf['obsidian_entrypoint_path_str']).resolve(),
        'md_entrypoint': Path(conf['md_entrypoint_path_str']).resolve(),
        'html_output_folder': Path(conf['html_output_folder_path_str']).resolve()
    }

    # Deduce relative paths
    paths['rel_obsidian_entrypoint'] = paths['obsidian_entrypoint'].relative_to(paths['obsidian_folder'])
    paths['rel_md_entrypoint_path']  = paths['md_entrypoint'].relative_to(paths['md_folder'])


    # Compile dynamic inclusion list
    # ---------------------------------------------------------
    # This is a set of javascript/css files to be loaded into the header based on config choices.
    dynamic_inclusions = ""
    if conf['toggles']['features']['build_graph']:
        dynamic_inclusions += '<link rel="stylesheet" href="/98682199-5ac9-448c-afc8-23ab7359a91b-static/graph.css" />' + "\n"
        dynamic_inclusions += '<script src="https://d3js.org/d3.v4.min.js"></script>' + "\n"


    # Remove previous output
    # ---------------------------------------------------------
    if conf['toggles']['no_clean'] == False:
        print('> CLEARING OUTPUT FOLDERS')
        if conf['toggles']['compile_md']:
            if paths['md_folder'].exists():
                shutil.rmtree(paths['md_folder'])

        if paths['html_output_folder'].exists():
            shutil.rmtree(paths['html_output_folder'])    

    # Recreate folder tree
    # ---------------------------------------------------------
    print('> CREATING OUTPUT FOLDERS')
    paths['md_folder'].mkdir(parents=True, exist_ok=True)
    paths['html_output_folder'].mkdir(parents=True, exist_ok=True)

    # Make "global" object that we can pass to functions
    # ---------------------------------------------------------
    pb = PicknickBasket(conf, paths)

    # Convert Obsidian to markdown
    # ---------------------------------------------------------
    if conf['toggles']['compile_md']:

        # Load all filenames in the root folder.
        # This data will be used to check which files are local, and to get their full path
        # It's clear that no two files can be allowed to have the same file name.
        files = {}
        for path in paths['obsidian_folder'].rglob('*'):
            if path.is_dir():
                continue

            # Exclude configured subfolders
            if 'exclude_subfolders' in conf:
                _continue = False
                for folder in conf['exclude_subfolders']:
                    excl_folder_path = paths['obsidian_folder'].joinpath(folder)
                    if path.resolve().is_relative_to(excl_folder_path):
                        if conf['toggles']['verbose_printout']:
                            print(f'Excluded folder {excl_folder_path}: Excluded file {path.name}.')
                        _continue = True
                    continue
                if _continue:
                    continue

            # Check if filename is duplicate
            if path.name in files.keys() and conf['toggles']['allow_duplicate_filenames_in_root'] == False:
                print(path)
                raise DuplicateFileNameInRoot(f"Two or more files with the name \"{path.name}\" exist in the root folder. See {str(path)} and {files[path.name]['fullpath']}.")

            # Add to tree
            files[path.name] = {'fullpath': str(path), 'processed': False}  

        pb.files = files

        # Start conversion with entrypoint.
        # Note: this will mean that any note not (indirectly) linked by the entrypoint will not be included in the output!
        print(f'> COMPILING MARKDOWN FROM OBSIDIAN CODE ({str(paths["obsidian_entrypoint"])})')
        recurseObisidianToMarkdown(str(paths['obsidian_entrypoint']), pb)

        # Keep going until all other files are processed
        if conf['toggles']['process_all'] == True:
            unparsed = {}
            for k in files.keys():
                if files[k]["processed"] == False:
                    unparsed[k] = files[k]

            i = 0
            l = len(unparsed.keys())
            for k in unparsed.keys():
                i += 1
                if conf['toggles']['verbose_printout'] == True:
                    print(f'{i}/{l}')
                recurseObisidianToMarkdown(unparsed[k]['fullpath'], pb)

        

    # Convert Markdown to Html
    # ------------------------------------------
    if conf['toggles']['compile_html']:
        print(f'> COMPILING HTML FROM MARKDOWN CODE ({str(paths["md_entrypoint"])})')

        # Get html template code. 
        # Every note will become a html page, where the body comes from the note's markdown, 
        # and the wrapper code from this template.
        if  'html_template_path_str' in conf.keys() and conf['html_template_path_str'] != '':
            print('-------------')
            with open(Path(conf['html_template_path_str']).resolve()) as f:
                html_template = f.read()
        else:
            html_template = OpenIncludedFile('template.html')

        if '{content}' not in html_template:
            raise Exception('The provided html template does not contain the string `{content}`. This will break its intended use as a template.')
            exit(1)

        # Load all filenames in the markdown folder
        # This data is used to check which links are local
        files = {}
        for path in paths['md_folder'].rglob('*'):
            if path.is_dir():
                continue
            rel_path_posix = path.relative_to(paths['md_folder']).as_posix()
            files[rel_path_posix] = {'fullpath': str(path.resolve()), 'processed': False}  

        pb.files = files
        pb.html_template = html_template
        pb.dynamic_inclusions = dynamic_inclusions

        # Start conversion from the entrypoint
        ConvertMarkdownPageToHtmlPage(str(paths['md_entrypoint']), pb)

        # Keep going until all other files are processed
        if conf['toggles']['process_all'] == True:
            unparsed = {}
            for k in files.keys():
                if files[k]["processed"] == False:
                    unparsed[k] = files[k]

            i = 0
            l = len(unparsed.keys())
            for k in unparsed.keys():
                i += 1
                if conf['toggles']['verbose_printout'] == True:
                    print(f'{i}/{l}')
                ConvertMarkdownPageToHtmlPage(unparsed[k]['fullpath'], pb)

        # Create tag page
        recurseTagList(pb.tagtree, 'tags/', pb, level=0)

        # Add Extra stuff to the output directories
        ExportStaticFiles(pb)

        # Write node json to static folder
        with open (pb.paths['html_output_folder'].joinpath('98682199-5ac9-448c-afc8-23ab7359a91b-static').joinpath('graph.json'), 'w', encoding="utf-8") as f:
            f.write(pb.network_tree.OutputJson())

        

    print('> DONE')
