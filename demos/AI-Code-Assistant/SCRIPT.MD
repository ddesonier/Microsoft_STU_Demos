# 1. Code Generation and Completion
## Scenario: Building a basic REST API endpoint in Python using Flask.
### Prompt: Write a basic REST API endpoint in Python using Flask

# 2. Error Detection and Debugging
## Scenario: Detecting and suggesting fixes for a faulty Python function.

### Prompt: Check this code

    def divide_numbers(a, b):
        return a / b

# 3. Code Refactoring
## Scenario: Cleaning up repetitive code.

### Prompt: Clean up this code:

    def square(x):
        return x * x

    def cube(x):
        return x * x * x 


# 4. Testing and Quality Assurance
## Scenario: Generating unit tests for a function.

### Prompt: Generate unit tests for this code

    def add_numbers(a, b):
        return a + b

# 5. Natural Language Processing (NLP)
## Scenario: Generating Python code from a plain English description.

### Prompt: Create a function in Python that calculates the factorial of a number using recursion.

# 6. Documentation
## Scenario: Automatically generating API documentation for a Python Flask app.

### Prompt: Generate API Documentation for the following Flask App


    @app.route('/api/data', methods=['GET'])
    def get_data():
        """
        Endpoint: /api/data
        Method: GET
        Description: Fetches a welcome message from the API.
        Returns: JSON object with a welcome message.
        """
        data = {"message": "Welcome to the API"}
    return jsonify(data)

# 7. Learning and Skill Enhancement
## Scenario: Providing a personalized code review and suggestions for improvement.

### Prompt: Review this code and make suggestions for improvements

    def find_max(lst):
        max_num = lst[0]
        for num in lst:  
            if num > max_num:  
                max_num = num  
        return max_num  
