const form = document.getElementsByClassName('form-signup')[0];

const showAlert = (cssClass, message) => {
  const html = `
    <div class="alert alert-${cssClass} alert-dismissible" role="alert">
        <strong>${message}</strong>
        <button class="close" type="button" data-dismiss="alert" aria-label="Close">
            <span aria-hidden="true">×</span>
        </button>
    </div>`;
  document.querySelector('#alert').innerHTML += html;
};

const formToJSON = (elements) =>
  [].reduce.call(elements, (data, element) => {
    data[element.name] = element.value;
    return data;
  }, {});

const handleFormSubmit = (event) => {
  console.log('signup submit');
  event.preventDefault();
  const postUrl = `/login`;

  const data = formToJSON(form.elements);
  const xhr = new XMLHttpRequest();
  xhr.open('POST', postUrl, true);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.send(JSON.stringify(data));

  xhr.onreadystatechange = () => {
    if (xhr.readyState === XMLHttpRequest.DONE) {
      showAlert('primary', xhr.responseText);
      console.log(xhr.responseText);
    }
  };
};

form.addEventListener('submit', handleFormSubmit);
