import os
import glob

def refactor():
    for root, _, files in os.walk(r'd:\SmartLex\src\smartlex'):
        for file in files:
            if file.endswith('.py'):
                filepath = os.path.join(root, file)
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                content = content.replace('from core.', 'from smartlex.core.')
                content = content.replace('import core.', 'import smartlex.core.')
                content = content.replace('from gui.', 'from smartlex.gui.')
                content = content.replace('import gui.', 'import smartlex.gui.')
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                    
if __name__ == '__main__':
    refactor()
